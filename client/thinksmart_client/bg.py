"""Background tasks nobody awaits. asyncio only keeps weak references to tasks, so one that is waiting on
something nothing else holds can be garbage-collected mid-run; keep them here until they finish, and log a
failure (otherwise it only shows as "Task exception was never retrieved", if at all)."""
import asyncio
import logging

log = logging.getLogger(__name__)
_live = set()


def spawn(coro, name=None):
    task = asyncio.create_task(coro, name=name)
    _live.add(task)
    task.add_done_callback(_done)
    return task


def _done(task):
    _live.discard(task)
    if not task.cancelled() and task.exception() is not None:
        log.error('background task %s failed', task.get_name(), exc_info=task.exception())
