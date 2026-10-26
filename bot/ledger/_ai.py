"""Ledger domain mixin: LedgerAiMixin — provider health and the daily answer budget.

Everything here is state the assistant needs to survive a restart or to be
shared between the three processes that ask questions (uvicorn, the bot, the
agent). Clock comparisons happen inside SQL against ``now()`` on purpose: the
web process and the bot process are not guaranteed to agree on the wall clock,
and a parked provider whose deadline came from a client clock would un-park
early or late depending on who wrote it.
"""


class LedgerAiMixin:
    def ai_provider_rows(self) -> list[dict]:
        """Every provider this database has ever heard of, with its state."""
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT id, name, url, model, key_env, fail_count,
                       (parked_until IS NOT NULL AND parked_until > now()) AS parked,
                       last_error
                  FROM ai_provider
                """
            )
            return [dict(r) for r in cur.fetchall()]

    def ai_provider_ok(self, pid: str, name: str, url: str, model: str, key_env: str) -> None:
        """A provider answered: clear its failure streak and any park."""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO ai_provider (id, name, url, model, key_env, fail_count,
                                         parked_until, last_error, updated_at)
                VALUES (%(pid)s, %(name)s, %(url)s, %(model)s, %(key_env)s, 0,
                        NULL, NULL, now())
                ON CONFLICT (id) DO UPDATE
                    SET name = %(name)s, url = %(url)s, model = %(model)s,
                        key_env = %(key_env)s, fail_count = 0,
                        parked_until = NULL, last_error = NULL, updated_at = now()
                """,
                {"pid": pid, "name": name, "url": url, "model": model, "key_env": key_env},
            )
            self._conn.commit()

    def ai_provider_fail(
        self, pid: str, name: str, url: str, model: str, key_env: str, error: str, park_seconds: int
    ) -> None:
        """Park a provider for ``park_seconds`` and remember why, truncated:
        provider error bodies can carry request payloads."""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO ai_provider (id, name, url, model, key_env, fail_count,
                                         parked_until, last_error, updated_at)
                VALUES (%(pid)s, %(name)s, %(url)s, %(model)s, %(key_env)s, 1,
                        now() + %(park)s * interval '1 second', %(error)s, now())
                ON CONFLICT (id) DO UPDATE
                    SET name = %(name)s, url = %(url)s, model = %(model)s,
                        key_env = %(key_env)s,
                        fail_count = ai_provider.fail_count + 1,
                        parked_until = now() + %(park)s * interval '1 second',
                        last_error = %(error)s, updated_at = now()
                """,
                {
                    "pid": pid,
                    "name": name,
                    "url": url,
                    "model": model,
                    "key_env": key_env,
                    "park": int(park_seconds),
                    "error": (error or "")[:300],
                },
            )
            self._conn.commit()

    def ai_reserve_answer(self, cap: int) -> bool:
        """Take one slot of today's answer budget across all processes.

        False means the budget is spent. One statement, so two processes racing
        on the last slot cannot both be told yes.
        """
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO ai_usage_day (day, answers)
                VALUES (CURRENT_DATE, 1)
                ON CONFLICT (day) DO UPDATE
                    SET answers = ai_usage_day.answers + 1
                    WHERE ai_usage_day.answers < %(cap)s
                RETURNING answers
                """,
                {"cap": int(cap)},
            )
            row = cur.fetchone()
            self._conn.commit()
        return row is not None

    def ai_usage_today(self) -> int:
        """Answers already served today (0 before the first one)."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT answers FROM ai_usage_day WHERE day = CURRENT_DATE"
            )
            row = cur.fetchone()
        return int(row["answers"]) if row else 0
