"""Fault-injection hook points.

Production uses NoFaults, whose hit() does nothing. Tests pass their own
object with the same hit(point) method to raise from named points:

    activity.after_insert   after the activity INSERT, before the lead UPDATE
    before_commit           after audit + idempotency inserts, just before COMMIT
"""


class NoFaults:
    def hit(self, point):
        return None
