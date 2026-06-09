"""SQLite CRUD for goals, plans, plan_steps.

Phase 0 already created the tables. This module just adds operations.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pure_agent.persistence.db import Database
from pure_agent.plan.models import (
    Goal,
    GoalConstraints,
    GoalStatus,
    Plan,
    PlanStatus,
    PlanStep,
    StepKind,
    StepReport,
    StepStatus,
    now_iso,
)


class PlanStorage:
    """Persistence layer for Goal / Plan / PlanStep."""

    def __init__(self, db: Database) -> None:
        self.db = db
        # ensure there's a 'default' project for tests/CLI without explicit project creation
        cur = db.conn.execute("SELECT id FROM projects WHERE id = 'default'")
        if cur.fetchone() is None:
            now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
            db.conn.execute(
                "INSERT OR IGNORE INTO projects (id, name, hash, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("default", "Default", "default", now, now),
            )

    # ─── goal CRUD ────────────────────────────────────────────────────────

    def create_goal(self, project_id: str, text: str, constraints: GoalConstraints | None = None) -> Goal:
        g = Goal(project_id=project_id, text=text, constraints=constraints or GoalConstraints())
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO goals (id, project_id, text, constraints_json, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    g.id,
                    g.project_id,
                    g.text,
                    g.constraints.model_dump_json(),
                    g.status.value,
                    g.created_at,
                    g.updated_at,
                ),
            )
        return g

    def get_goal(self, goal_id: str) -> Goal | None:
        row = self.db.conn.execute(
            "SELECT * FROM goals WHERE id = ?", (goal_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_goal(row)

    def list_goals(self, project_id: str | None = None) -> list[Goal]:
        if project_id is not None:
            rows = self.db.conn.execute(
                "SELECT * FROM goals WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.db.conn.execute(
                "SELECT * FROM goals ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_goal(r) for r in rows]

    def update_goal_status(self, goal_id: str, status: GoalStatus) -> None:
        self.db.conn.execute(
            "UPDATE goals SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, now_iso(), goal_id),
        )

    @staticmethod
    def _row_to_goal(row) -> Goal:
        constraints = GoalConstraints.model_validate_json(row["constraints_json"] or "{}")
        return Goal(
            id=row["id"],
            project_id=row["project_id"],
            text=row["text"],
            constraints=constraints,
            status=GoalStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ─── plan CRUD ────────────────────────────────────────────────────────

    def create_plan(self, plan: Plan) -> Plan:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO plans (id, goal_id, version, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    plan.id,
                    plan.goal_id,
                    plan.version,
                    plan.status.value,
                    plan.created_at,
                    plan.updated_at,
                ),
            )
            for s in plan.steps:
                self._insert_step(conn, s)
        return plan

    def get_plan(self, plan_id: str) -> Plan | None:
        row = self.db.conn.execute(
            "SELECT * FROM plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            return None
        steps = self.list_steps(plan_id)
        return self._row_to_plan(row, steps)

    def list_plans(self, goal_id: str | None = None) -> list[Plan]:
        if goal_id is not None:
            rows = self.db.conn.execute(
                "SELECT * FROM plans WHERE goal_id = ? ORDER BY created_at DESC",
                (goal_id,),
            ).fetchall()
        else:
            rows = self.db.conn.execute(
                "SELECT * FROM plans ORDER BY created_at DESC"
            ).fetchall()
        out = []
        for r in rows:
            steps = self.list_steps(r["id"])
            out.append(self._row_to_plan(r, steps))
        return out

    def update_plan_status(self, plan_id: str, status: PlanStatus) -> None:
        self.db.conn.execute(
            "UPDATE plans SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, now_iso(), plan_id),
        )

    def increment_plan_version(self, plan_id: str) -> int:
        self.db.conn.execute(
            "UPDATE plans SET version = version + 1, updated_at = ? WHERE id = ?",
            (now_iso(), plan_id),
        )
        row = self.db.conn.execute(
            "SELECT version FROM plans WHERE id = ?", (plan_id,)
        ).fetchone()
        return int(row["version"])

    @staticmethod
    def _row_to_plan(row, steps: list[PlanStep]) -> Plan:
        return Plan(
            id=row["id"],
            goal_id=row["goal_id"],
            version=row["version"],
            status=PlanStatus(row["status"]),
            steps=steps,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ─── step CRUD ────────────────────────────────────────────────────────

    def _insert_step(self, conn, s: PlanStep) -> None:
        conn.execute(
            "INSERT INTO plan_steps (id, plan_id, idx, kind, action, deps_json, status, "
            "assigned_subagent, attempts, max_attempts, last_error, started_at, completed_at, step_report_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                s.id,
                s.plan_id,
                s.idx,
                s.kind.value,
                s.action,
                json.dumps(s.deps),
                s.status.value,
                s.assigned_subagent,
                s.attempts,
                s.max_attempts,
                s.last_error,
                s.started_at,
                s.completed_at,
                s.step_report.model_dump_json() if s.step_report else None,
            ),
        )

    def list_steps(self, plan_id: str) -> list[PlanStep]:
        rows = self.db.conn.execute(
            "SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY idx", (plan_id,)
        ).fetchall()
        return [self._row_to_step(r) for r in rows]

    def upsert_step(self, step: PlanStep) -> None:
        """Insert or replace a step (used by Runner to update status)."""
        existing = self.db.conn.execute(
            "SELECT id FROM plan_steps WHERE id = ?", (step.id,)
        ).fetchone()
        with self.db.transaction() as conn:
            if existing:
                conn.execute(
                    "UPDATE plan_steps SET status=?, attempts=?, last_error=?, started_at=?, "
                    "completed_at=?, step_report_json=?, assigned_subagent=?, deps_json=? "
                    "WHERE id = ?",
                    (
                        step.status.value,
                        step.attempts,
                        step.last_error,
                        step.started_at,
                        step.completed_at,
                        step.step_report.model_dump_json() if step.step_report else None,
                        step.assigned_subagent,
                        json.dumps(step.deps),
                        step.id,
                    ),
                )
            else:
                self._insert_step(conn, step)

    def delete_step(self, step_id: str) -> None:
        self.db.conn.execute("DELETE FROM plan_steps WHERE id = ?", (step_id,))

    @staticmethod
    def _row_to_step(row) -> PlanStep:
        deps = json.loads(row["deps_json"]) if row["deps_json"] else []
        report = None
        if row["step_report_json"]:
            report = StepReport.model_validate_json(row["step_report_json"])
        return PlanStep(
            id=row["id"],
            plan_id=row["plan_id"],
            idx=row["idx"],
            kind=StepKind(row["kind"]),
            action=row["action"],
            deps=deps,
            status=StepStatus(row["status"]),
            assigned_subagent=row["assigned_subagent"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            last_error=row["last_error"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            step_report=report,
        )

    # ─── transactional helpers ────────────────────────────────────────────

    @contextmanager
    def transaction(self) -> Iterator:
        with self.db.transaction() as conn:
            yield conn


__all__ = ["PlanStorage"]
