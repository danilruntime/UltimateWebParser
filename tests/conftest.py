"""Shared pytest fixtures for the uwp test suite."""
import threading


def worker_threads() -> set[str]:
    """Names of the threads ``aiosqlite`` starts, one per live connection.

    A leak shows up here as a surviving non-daemon thread: the interpreter does
    not exit while one is alive, so a container that hit the leak prints its
    error and then hangs instead of crash-looping.
    """
    return {
        thread.name
        for thread in threading.enumerate()
        if "_connection_worker_thread" in thread.name
    }
