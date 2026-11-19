"""Background tasks nobody awaits. asyncio only keeps weak references to tasks, so one that is waiting on
something nothing else holds can be garbage-collected mid-run; keep them here until they finish, and log a
failure (otherwise it only shows as "Task exception was never retrieved", if at all).

forever(): the client's standing loops (the conversation's timers, the sleep hours, the weather...). One
unexpected error used to end such a loop for good, silently stopping its feature until the client was
restarted; now the error is logged and the loop starts again after a pause."""
import asyncio
import logging

log = logging.getLogger(__name__)
_live = set()
restarts = {}   # 常驻循环出错重启的次数（forever），设备健康上报里带上（设计画布第 83 块）
RESTART_AFTER = (1, 5, 30, 60)   # seconds before restarting a loop after its 1st, 2nd, 3rd... error in a row
CALM = 600                       # a loop that ran this long since its last error starts counting again


def spawn(coro, name=None):
    task = asyncio.create_task(coro, name=name)
    _live.add(task)
    task.add_done_callback(_done)
    return task


def _done(task):
    _live.discard(task)
    if not task.cancelled() and task.exception() is not None:
        log.error('background task %s failed', task.get_name(), exc_info=task.exception())


def forever(make, name=None):
    """Run make() (a coroutine function: a loop meant to run until the client stops); if it raises, log
    it and run it again. It returning normally ends it."""
    name = name or getattr(make, '__name__', 'loop')

    async def supervise():
        errors, loop = 0, asyncio.get_running_loop()
        while True:
            started = loop.time()
            try:
                await make()
                return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001  anything: the loop's feature must not stop for good
                errors = 1 if loop.time() - started > CALM else errors + 1
                restarts[name] = restarts.get(name, 0) + 1
                pause = RESTART_AFTER[min(errors, len(RESTART_AFTER)) - 1]
                log.exception('%s failed (%d in a row): starting it again in %d s', name, errors, pause)
                await asyncio.sleep(pause)
    return spawn(supervise(), name=name)
