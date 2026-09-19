"""Bounded transitive source validation, not transitive evidence serialization.

Call under contracts -> policy locks. Cooperative plan/artifact writers therefore
cannot change a pin between validation and a local save; external direct edits
that ignore advisory locks remain outside that transaction boundary. Only compact
headers and current revisions are memoized, never full ancestry payloads.

Policy independence is deliberate: descendant historical policies do not expire
extracted features. Adoption of a proposal's directly selected insight does require
that insight's policy revision to be current. Re-reduction can use historical
insights as ancestry, but every reachable plan pin and artifact hash is rechecked.
"""
from __future__ import annotations

from agent_memory.contracts_store import ContractError
from agent_memory.rsi_schema import MAX_ARTIFACT_BYTES, MAX_PLAN_FILE_BYTES, artifact_revision

MAX_LINEAGE_ARTIFACTS = 4096
MAX_LINEAGE_PLANS = 1024
MAX_LINEAGE_BYTES = 64 * 1024 * 1024
MAX_LINEAGE_EDGES = 65536
MAX_LINEAGE_PROBLEMS = 32


class LineageCheck:
    """Iterative DFS with unique-source memoization and explicit cycle states."""

    def __init__(self, store, plan_reader):
        self.store, self.plan_reader = store, plan_reader
        self.artifacts, self.plans, self.states = {}, {}, {}
        self.problems, self.problem_keys = [], set()
        self.complete, self.bytes_read, self.edges = True, 0, 0
        self.exhausted = False

    def problem(self, kind, entry_id, code, *, expected=None, current=None, unavailable=False):
        key = (kind, entry_id, code, expected, current)
        if key not in self.problem_keys:
            self.problem_keys.add(key)
            if len(self.problems) < MAX_LINEAGE_PROBLEMS:
                problem = {"kind": kind, "id": entry_id, "code": code}
                if expected is not None:
                    problem["expected_revision"] = expected
                    problem["current_revision"] = current
                self.problems.append(problem)
        if unavailable:
            self.complete = False

    def limit(self, code):
        self.exhausted = True
        self.problem("lineage", None, code, unavailable=True)

    def charge(self, path, maximum):
        if not path.is_file() or path.stat().st_size > maximum:
            raise ContractError("source is not a bounded regular file")
        size = path.stat().st_size
        if self.bytes_read + size > MAX_LINEAGE_BYTES:
            self.limit("byte_limit")
            return False
        self.bytes_read += size
        return True

    def artifact(self, entry_id):
        if entry_id in self.artifacts:
            return self.artifacts[entry_id]
        if len(self.artifacts) >= MAX_LINEAGE_ARTIFACTS:
            self.limit("artifact_limit")
            return None
        self.artifacts[entry_id] = None
        try:
            if not self.charge(self.store.path(entry_id), MAX_ARTIFACT_BYTES):
                return None
            value = self.store.read(entry_id)
            header = {"revision": artifact_revision(value), "kind": value["kind"],
                      "policy_revision": value["bindings"]["policy_revision"],
                      "plans": value["bindings"]["plans"],
                      "artifacts": value["bindings"].get("artifacts", [])}
            self.artifacts[entry_id] = header
            return header
        except (ValueError, OSError, RuntimeError, TypeError, KeyError):
            self.problem("artifact", entry_id, "unavailable", unavailable=True)
            return None

    def plan(self, pin):
        entry_id = pin["plan_id"]
        if entry_id not in self.plans:
            if len(self.plans) >= MAX_LINEAGE_PLANS:
                self.limit("plan_limit")
                return
            self.plans[entry_id] = None
            try:
                if not self.charge(self.store.contracts.path("plans", entry_id), MAX_PLAN_FILE_BYTES):
                    return
                self.plans[entry_id] = self.plan_reader(self.store, entry_id)["revision"]
            except (ValueError, OSError, RuntimeError, TypeError, KeyError):
                self.problem("plan", entry_id, "unavailable", unavailable=True)
        current = self.plans[entry_id]
        if current is not None and current != pin["revision"]:
            self.problem("plan", entry_id, "revision_mismatch", expected=pin["revision"], current=current)

    def inspect(self, bound, *, policy_revision, adopt_insights=False):
        for pin in bound["plans"]:
            self.plan(pin)
            if self.exhausted:
                break
        # Enter/exit events avoid Python recursion even for deep acyclic graphs.
        stack = [("enter", ref, True) for ref in reversed(bound.get("artifacts", []))]
        while stack and not self.exhausted:
            action, ref, direct = stack.pop()
            if action == "exit":
                self.states[ref] = "done"
                continue
            if self.edges >= MAX_LINEAGE_EDGES:
                self.limit("edge_limit")
                break
            self.edges += 1
            entry_id = ref["id"]
            header = self.artifact(entry_id)
            if header is None:
                continue
            if header["revision"] != ref["revision"]:
                self.problem("artifact", entry_id, "revision_mismatch", expected=ref["revision"], current=header["revision"])
            if direct and adopt_insights and header["kind"] == "insight":
                if policy_revision is None:
                    self.problem("policy", entry_id, "unavailable", unavailable=True)
                elif header["policy_revision"] != policy_revision:
                    self.problem("policy", entry_id, "revision_mismatch", expected=header["policy_revision"], current=policy_revision)
            state = self.states.get(entry_id)
            if state == "visiting":
                self.problem("artifact", entry_id, "cycle", unavailable=True)
                continue
            if state == "done":
                continue
            self.states[entry_id] = "visiting"
            stack.append(("exit", entry_id, False))
            for pin in header["plans"]:
                self.plan(pin)
                if self.exhausted:
                    break
            stack.extend(("enter", child, False) for child in reversed(header["artifacts"]))
        status = "unavailable" if not self.complete else "stale" if self.problem_keys else "current"
        summary = {"status": status, "complete": self.complete,
                   "artifacts_checked": len(self.artifacts), "plans_checked": len(self.plans),
                   "edges_checked": self.edges, "bytes_checked": self.bytes_read,
                   "problem_count": len(self.problem_keys), "problems": self.problems,
                   "problems_truncated": len(self.problem_keys) > len(self.problems)}
        return summary

    def direct_plans(self, bound):
        return [{**pin, "current_revision": self.plans.get(pin["plan_id"]),
                 "status": self._status(pin["revision"], self.plans.get(pin["plan_id"]))}
                for pin in bound["plans"]]

    def direct_artifacts(self, bound):
        result = []
        for ref in bound.get("artifacts", []):
            header = self.artifacts.get(ref["id"])
            current = header["revision"] if header is not None else None
            result.append({**ref, "current_revision": current, "status": self._status(ref["revision"], current)})
        return result

    @staticmethod
    def _status(expected, current):
        return "unavailable" if current is None else "current" if expected == current else "stale"


def require_lineage(store, bound, plan_reader, *, policy_revision, adopt_insights=False):
    result = LineageCheck(store, plan_reader).inspect(bound, policy_revision=policy_revision, adopt_insights=adopt_insights)
    if result["status"] != "current":
        problem = result["problems"][0]
        raise ContractError(f"stale or unavailable RSI lineage: {problem['kind']} {problem['id'] or ''} {problem['code']}")
    return result
