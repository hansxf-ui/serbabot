"""Logika antrean & pairing anon-chat. MURNI Python — tanpa import telegram,
biar bisa di-unit-test tanpa network."""

import time


class PairingManager:
    def __init__(self, timeout=600):
        self.timeout = timeout  # detik tanpa pesan -> sesi diputus
        self._queue = []        # [user_id] yang lagi nunggu
        self._pairs = {}        # user_id -> partner_id
        self._last_active = {}  # user_id -> timestamp

    def search(self, user_id):
        """Return (status, partner_id|None).
        status: 'already' | 'paired' | 'queued'"""
        if user_id in self._pairs:
            return ("already", self._pairs[user_id])
        if user_id in self._queue:
            return ("queued", None)
        for other in list(self._queue):
            if other != user_id:
                self._queue.remove(other)
                self._pairs[user_id] = other
                self._pairs[other] = user_id
                now = time.time()
                self._last_active[user_id] = now
                self._last_active[other] = now
                return ("paired", other)
        self._queue.append(user_id)
        return ("queued", None)

    def stop(self, user_id):
        """Return (status, partner_id|None).
        status: 'unpaired' | 'dequeued' | 'idle'"""
        partner = self._pairs.pop(user_id, None)
        if partner is not None:
            self._pairs.pop(partner, None)
            self._last_active.pop(user_id, None)
            self._last_active.pop(partner, None)
            return ("unpaired", partner)
        if user_id in self._queue:
            self._queue.remove(user_id)
            return ("dequeued", None)
        return ("idle", None)

    def get_partner(self, user_id):
        return self._pairs.get(user_id)

    def touch(self, user_id):
        self._last_active[user_id] = time.time()

    def expired(self, now=None):
        """user_id yang sesi-nya lewat timeout (tanpa pesan)."""
        now = now if now is not None else time.time()
        return [u for u, t in self._last_active.items()
                if now - t > self.timeout]
