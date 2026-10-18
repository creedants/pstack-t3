
class QueueBase:

    def stored_heartbeat_ms(self):
        """The JSON text under the contract key heartbeatMs, or None when the key is absent."""
        with self.raw_db() as db:
            row = db.execute("SELECT value FROM contract WHERE key = 'heartbeatMs'").fetchone()
        return row and row[0]

    def stored_merge_heartbeat_ms(self):
        """The JSON text under the contract key mergeHeartbeatMs, or None when the key is absent."""
        with self.raw_db() as db:
            row = db.execute("SELECT value FROM contract WHERE key = 'mergeHeartbeatMs'").fetchone()
        return row and row[0]

    def stored_pr_item_line(self):
        """The JSON text under the contract key prItemLine, or None when the key is absent."""
        with self.raw_db() as db:
            row = db.execute("SELECT value FROM contract WHERE key = 'prItemLine'").fetchone()
        return row and row[0]
