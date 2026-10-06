"""PyMuPDF ist nicht threadsicher: alle Zugriffe aus verschiedenen Threads über diese Sperre."""

import threading

LOCK = threading.RLock()
