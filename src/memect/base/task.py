import asyncio
import gc
import logging
import os
import shutil
import sys
import threading
import time
import traceback
import uuid
import weakref
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from enum import StrEnum, auto
from pathlib import Path
from typing import Any, Callable, Final, Literal, Mapping, Sequence, final

from pydantic import BaseModel

from .api import ApiError
from .utils import MyBaseModel, Timer


class TaskStatus(StrEnum):
    waiting = auto()
    running = auto()
    done = auto()


class TaskProgress(BaseModel):
    name: str
    percent: float
    total: int | None = None
    progress: int | None = None
    elapsed: float | None = None


class StoppedError(Exception):
    pass
class Runner:
    """执行操作"""
    _logger=logging.getLogger(f'{__module__}.{__qualname__}')
    _verbose=True
    def __init__(self):
        self._stop_event:Final=threading.Event()

    def __del__(self):
        self._logger.debug('gc %s',self)

    def _run(self,task:'Task')->Any:
        pass

    def _mp_run(self,fn:Callable[[],Any],result:Any,wait:float=10,daemon:bool=False)->Any:
        import multiprocessing as mp
        p=None
        try:
            #不能够daemon=True，因为进程又创建了子进程
            p = mp.get_context("spawn").Process(target=fn, daemon=daemon)
            p.start()
            while p.is_alive():
                # 表示被终止了
                if self._stop_event.is_set():
                    break
                else:
                    #等待一会
                    p.join(1)

            if p.exitcode is None:
                #进程还在执行，但是任务被终止，所以准备杀死进程
                p.terminate()
                p.join(wait)
                if p.exitcode is None:
                    #kill -9，100%被杀死
                    p.kill()
                    #避免成为僵尸进程
                    p.join()
                    

            if p.exitcode is None:
                # 前面使用了kill，不应该执行到这里
                raise ApiError(ApiError.ANY, "任务执行失败")
            elif p.exitcode == 0:
                # 执行成功
                return result
            elif p.exitcode > 0:
                # 进程抛出异常？
                raise ApiError(ApiError.ANY, "任务执行失败")
            elif p.exitcode < 0:
                # 进程被kill？被外部kill或者前面kill（因为被停止了）
                raise ApiError(ApiError.ANY, "任务执行失败")
            else:
                raise RuntimeError("不可能执行到这里")
        finally:
            if p is not None:
                p.close()

    @final
    def run(self,task:'Task')->Any:
        try:
            return self._run(task)
        except StoppedError:
            #实际上记录这个异常就可以，因为任务被取消了（如：timeout，但是任务无法停止，还在执行）
            #现在获得这个异常，表示任务知道被取消，自动停止了
            self._logger.exception('任务被取消，但是操作还在执行，现在才停止')
            raise
        except BaseException:
            #如果任务为正常运行，那么这个异常会被_run_task()中获得，然后在poll()轮训的时候显示
            #如果任务被取消，那么，任务还在执行，再出现的异常就不能够获得了，所以在这里先显示一次？
            #就可能会显示2次相同的错误
            if self._verbose:
                self._logger.exception('执行任务出现异常')
            raise

    def stop(self):
        """如超时等，尽快停止"""
        self._stop_event.set()
    
    def is_stopped(self)->bool:
        return self._stop_event.is_set()

    
class Saver:
    """在执行任务失败后，可以执行save，保存错误信息，为了简化，仅仅处理执行出现的错误，如果还没有执行被取消等，不保存"""
    _logger=logging.getLogger(f'{__module__}.{__qualname__}')
    def __init__(self,dir:Path,files:Sequence[Path]|None=None):
        super().__init__()
        self.dir:Path=dir
        self.files:Final=files or ()
    
    def __del__(self):
        #self._logger.debug('gc %s',self)
        pass

    def save(self,msg:str):
        self.dir.mkdir(parents=True,exist_ok=True)
        for file in self.files:
            if file.is_file():
                shutil.copyfile(file,self.dir.joinpath(file.name))
            elif file.is_dir():
                shutil.copytree(file,self.dir/file.name)
            else:
                pass
        if msg:
            self.dir.joinpath('error.txt').write_text(msg,'utf-8')

class Task:
    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(
        self,
        id: str | None,
        object: Any,
        runner: Runner,
        custom_id: str | None = None,
        priority: int = 1,
        async_: bool = False,
        saver:Saver|None=None,
        details:Mapping[str,Any]|None=None
    ):
        super().__init__()
        self.id: Final = id or self.next_id()
        self.custom_id: Final = custom_id
        """表示自定义的id，在返回task和查询的时候，使用这个id"""
        self.priority: Final = priority
        self.object: Final = object
        """可以绑定任意一个对象"""

        self.future: Final[asyncio.Future[Any]] = asyncio.Future()
        self._task: asyncio.Task[Any] | None = None
        """运行的task对象"""
        self._runner = runner
        self._saver = saver

        self._create_clock: float = time.monotonic()
        self._start_clock: float = 0
        self._end_clock: float = 0

        #记录的是秒，后续输出需要转换为毫秒int(ms*1000)
        self._create_time:float=time.time()
        self._start_time:float=0
        self._end_time:float=0

        self._async: Final = async_
        """True表示为异步返回结果"""

        self._progresses: dict[str, TaskProgress] = {}
        self._lock: Final = threading.RLock()

        self._details:Final=details
        """可以提供一下信息方便查看任务说明"""

        self.task_manager: TaskManager | None = None
        """在add后添加"""

        self._cancel_msg:str|None=None

        self.future.add_done_callback(self._on_future_done)
 
    def __del__(self):
        #循环引用不一定会除非，但是目前没有
        self._logger.debug('gc task,id=%s',self.id)

    def _release(self):
        """异步返回结果，在放入done_tasks，必须调用这个方法释放不再需要的内容"""
        #当对象被放进done_tasks等待用户结果，就可以释放部分资源了
        #所以先解除不需要的引用，让对象可以快速回收
        self._saver=None
        if self._runner is not None:
            self._runner.stop()
            self._runner=None
        

    def set_progress(
        self,
        name: str,
        *,
        percent: float | None = None,
        total: int | None = None,
        progress: int | None = None,
        elapsed: float | None = None,
    ):
        """允许在多线程下访问"""
        with self._lock:
            if percent is None:
                assert total is not None and total > 0
                assert progress is not None
                percent = progress / total
            self._progresses[name] = TaskProgress(
                name=name,
                percent=percent,
                total=total,
                progress=progress,
                elapsed=elapsed,
            )
        pass

    def get_progresses(self) -> dict[str, TaskProgress]:
        """允许在多线程访问，返回一个副本"""
        with self._lock:
            return dict(self._progresses)

    def status(self) -> TaskStatus:
        if self.future.done():
            return TaskStatus.done
        elif self._task is None:
            return TaskStatus.waiting
        else:
            return TaskStatus.running

    def start(self, timeout: float | None = None):
        assert self._task is None
        self._task = asyncio.create_task(self._run_task(timeout))
        self._task.add_done_callback(self._on_task_done)

    def poll(self) -> bool:
        """获得机会执行一次轮训，更新当前的完成进度，返回True表示完成了"""
        if self.future.done():
            # TODO 只会被轮训一次
            if self.future.cancelled():
                # 执行超时或者其他
                self._logger.error("任务被取消:%s", self.id)
            elif e := self.future.exception():
                self._logger.error(
                    "任务失败:%s", self.id, exc_info=(type(e), e, e.__traceback__)
                )
            else:
                # 任务成功
                self._logger.info("任务成功:%s", self.id)
            return True
        else:
            return False

    async def _run_task(self, timeout: float | None = None):
        runner = self._runner
        saver = self._saver
        try:
            self._start_clock = time.monotonic()
            self._start_time = time.time()
            if runner is not None:
                async with asyncio.timeout(timeout):
                    assert self.task_manager is not None
                    #如果因为超时退出，在线程中执行出现的异常，就不会获得了
                    #因为future被标记为被取消，如果runner.run继续执行且抛出异常，就没有地方显示
                    #所以最好的方案是run记录日志
                    #而如果没有超时，run抛出异常，这里仍然可以获得，就会重复显示异常，日志太乱
                    return await asyncio.get_running_loop().run_in_executor(
                        self.task_manager.executor, runner.run, self
                    )
            else:
                #可能在准备开始的时候，被取消了？
                raise ApiError(ApiError.ANY,'任务被取消')
        except asyncio.TimeoutError as e:
            raise ApiError(ApiError.ANY, "任务运行超时",timeout=timeout) from e
        finally:
            #无论是否有异常，都设置
            #如果是正常完成，设置不影响操作了
            #如果是异常，如：超时，runner可以检查是否被stop，然后停止操作，释放资源
            #释放引用，避免循环引用，可以快速回收对象
            if saver and sys.exc_info()[1] is not None:
                saver.save(traceback.format_exc())
            runner=None
            saver = None
            self._end_clock = time.monotonic()
            self._end_time = time.time()
            self._release()

    def _on_task_done(self, task: asyncio.Task[Any]):
        assert self._task is task
        self._task=None
        self._release()
        task.remove_done_callback(self._on_task_done)
        if not self.future.done():
            try:
                self.future.set_result(task.result())
            except asyncio.InvalidStateError:
                #不可能出现
                raise 
            except asyncio.CancelledError as e:
                msg:str|None=e.args[0] if len(e.args)>0 and isinstance(e.args[0],str) else None
                self.future.cancel(msg)
            except BaseException as e:
                self.future.set_exception(e)

    def _on_future_done(self,future:asyncio.Future[Any]):
        assert self.future is future
        #可能future因为超时先完成了
        self._release()
        if self._task and not self._task.done():
            self._task.cancel()
        

    def cancel(self, msg: str | None = None):
        """取消，如果已经完成，没有影响"""
        # 已经完成，没有影响
        self._release()
        if self._task is not None:
            self._task.cancel(msg)
        else:
            self.future.cancel(msg)
            if self._end_clock == 0:
                self._end_clock = time.monotonic()
                self._end_time = time.time()

    def is_timeout(
        self, stage: Literal["run", "wait", "done"], timeout: float | None
    ) -> bool:
        if timeout is None or timeout <= 0:
            return False

        # 表示在等待或者执行中
        if stage == "run":
            if self._start_clock > 0:
                return time.monotonic() - self._start_clock > timeout
            else:
                return False
        elif stage == "wait":
            return time.monotonic() - self._create_clock > timeout
        elif stage == "done":
            if self._end_clock > 0:
                return time.monotonic() - self._end_clock > timeout
            else:
                return False
        else:
            raise ValueError(f"不支持的stage={stage}")

    def ok(self) -> bool:
        """判断是否执行成功"""
        if self.future.done():
            if self.future.cancelled() or self.future.exception():
                return False
            else:
                return True
        else:
            return False

    def done(self) -> bool:
        """判断是否执行完毕（成功或者失败或者被取消）"""
        return self.future.done()

    async def result(self,timeout:float|None=None) -> Any:
        """等待结果，已经转换为ApiError，如果超过指定的时间，自动清除任务"""
        try:
            #return self.future.result()
            #超时后，会自动取消self.future
            return await asyncio.wait_for(self.future,timeout=timeout)
        except asyncio.TimeoutError as e:
            #超时后，会自动取消self.future
            self._cancel_msg='超过了用户指定的时间还没有完成任务'
            raise ApiError(ApiError.ANY,'超过了用户指定的时间还没有完成任务') from e
        except asyncio.InvalidStateError as e:
            #不应该出现这个错误
            raise ApiError(ApiError.ANY, "任务失败") from e
        except asyncio.CancelledError as e:
            #可能因为等待太长时间太长被取消，或者因为其他原因
            msg:str= '任务被取消'
            if self._cancel_msg:
                msg = self._cancel_msg
            elif len(e.args)>0 and isinstance(e.args[0],str):
                msg = e.args[0]
            else:
                pass
            raise ApiError(ApiError.ANY,msg) from e
        except ApiError:
            raise
        except BaseException as e:
            raise ApiError(ApiError.ANY, "任务失败") from e
        finally:
            pass

    def async_(self) -> bool:
        """True表示为异步返回结果"""
        return self._async

    def state(self) -> dict[str, Any]:
        return {
            "id": self.id,
            'custom_id':self.custom_id,
            "async": self.async_(),
            "status": self.status(),
            'priority':self.priority,
            #转换为ms
            'create_time': int(self._create_time*1000),
            'start_time': int(self._start_time*1000),
            'end_time': int(self._end_time*1000),
            "progresses": {
                k: v.model_dump_json() for k, v in self.get_progresses().items()
            },
            'details':self._details or {}
        }

    @classmethod
    def next_id(cls) -> str:
        # 总是使用北京时间？还是本地时区
        tz = timezone(timedelta(hours=8))
        t = datetime.now(tz)
        s = t.strftime("%Y%m%d%H%M%S")
        return f"{s}-{uuid.uuid4().hex}"



class TaskManagerArgs(MyBaseModel):
    max_running_size: int = 5
    max_waiting_size: int = 1000
    max_done_size: int = 1000

    max_running_timeout: float = 30 * 60
    max_waiting_timeout: float | None = None
    max_done_timeout: float = 30 * 60

    priorities: Mapping[int, int] | None = None

    gc_interval:float=5
    """如果为0，表示不需要定期gc"""




class TaskManager:
    """设计是在asyncio下使用，所以没有使用线程，多数方法不支持多线程使用，个别方法允许，
    使用场景，在FastApi下（或者类似的httpserver）下调度任务执行请求
    """

    _logger = logging.getLogger(f"{__module__}.{__qualname__}")

    def __init__(self, args: TaskManagerArgs | Mapping[str, Any] | None = None):
        super().__init__()
        args = TaskManagerArgs.create(args)
        self._running_tasks: Final[list[Task]] = []
        self._waiting_tasks: Final[list[Task]] = []
        self._done_tasks: Final[list[Task]] = []
        """存储完成的异步返回的任务"""

        self._task_ids:list[str]=[]
        """记录所有的task的id，用来gc"""

        self._max_running_size: Final = args.max_running_size
        """最大的执行任务数"""
        self._max_waiting_size: Final = args.max_waiting_size
        """最大的等待任务数，超过就拒绝"""
        self._max_done_size: Final = args.max_done_size
        """完成的异步任务队列的最大个数，超过这个值，就把前面的删除"""

        self._max_running_timeout: float = args.max_running_timeout
        """执行的最长时间，超过时间，就终止执行"""
        self._max_waiting_timeout: float | None = args.max_waiting_timeout
        """等待的最长时间，超过时间，就不再等待，，None或者0表示无限等待"""
        self._max_done_timeout: float = args.max_done_timeout
        """完成的任务，超过这个时间没有读取结果，就抛弃"""

        self._priorities: Final[Mapping[int, int]] = args.priorities or {}
        """设置每个优先级最多可以执行多少个任务，如：{1:2,2:3} 表示1的可以执行2过，2的可以执行3个，其他没有设置的，就是max_running_size"""
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[Any] | None = None

        self._total: int = 0
        self._error_total: int = 0
        self._success_total: int = 0

        #这里多几个的原因是：当有异常出现，如：超时，会已经结束任务返回，但是线程还在继续，任务不一定可以马上停止，也就是还占用一个线程
        #这时候如果有新的任务，可以调度，但是没有线程可以执行，所以多几个
        self.executor: Final = ThreadPoolExecutor(self._max_running_size + 5)
        """如果需要执行的Task是同步的操作，可以在这个线程池中执行"""

        self._timer: Final = Timer.start()
        self._start_time: float = 0

        self._last_gc_clock:float=time.monotonic()
        self._gc_interval:float=args.gc_interval

    def start(self):
        """开始，必须在异步环境下调用"""
        self._timer.mark("start")
        self._start_time = time.time()
        self._task = asyncio.create_task(self._run())

    def stop(self):
        self._timer.mark("stop")
        self._stop_event.set()

    async def astop(self):
        self._timer.mark('stop')
        self._stop_event.set()
        # 可以等待完成？
        if self._task is not None and not self._task.done():
            try:
                await self._task
            except asyncio.CancelledError:
                # ctrl+c引起
                pass
            except BaseException:
                self._logger.exception("")

    async def _run(self):
        self._logger.info("start run")
        try:
            while not self._stop_event.is_set():
                # self._print_state()
                self._poll_tasks()
                self._fetch_tasks()
                self._gc_tasks()
                await asyncio.sleep(0.1)
        finally:
            for task in self._running_tasks:
                task.cancel("系统退出")
            for task in self._waiting_tasks:
                task.cancel("系统退出")
            self._running_tasks.clear()
            self._waiting_tasks.clear()
            self._done_tasks.clear()
            self._logger.info("exit run,uptime=%s", self._timer.uptime())

    def _print_state(self):
        print(f"priorities={self._priorities}")
        print(
            f"waiting:{len(self._waiting_tasks)}/{self._max_waiting_size},timeout={self._max_waiting_timeout}"
        )
        print(
            f"running:{len(self._running_tasks)}/{self._max_running_size},timeout={self._max_running_timeout}"
        )
        print(
            f"done:{len(self._done_tasks)}/{self._max_done_size},timeout={self._max_done_timeout}"
        )

    def _poll_tasks(self):
        i = 0
        while i < len(self._running_tasks):
            task = self._running_tasks[i]
            if task.poll():
                self._logger.debug('remove task from running tasks,ok=%s,async=%s,id=%s',task.ok(),task.async_(),task.id)
                del self._running_tasks[i]
                if task.async_():
                    self._done_tasks.append(task)
                else:
                    # 如果是同步等待结果，不需要放到队列中
                    pass
                if task.ok():
                    self._success_total += 1
                else:
                    self._error_total += 1
            else:
                i += 1

        i = 0
        while i < len(self._waiting_tasks):
            task = self._waiting_tasks[i]
            if task.poll() or ( self._max_waiting_timeout and task.is_timeout(
                "wait", self._max_waiting_timeout
            )):
                if not task.done():
                    #如果没有被取消（api指定了时间没有开始），就是因为等待超时
                    task.cancel("等待超时")
                
                #超过api指定的时间，或者设置的等待时间
                self._logger.debug('remove task from waiting tasks,async=%s,id=%s',task.async_(),task.id)
                if task.async_():
                    self._done_tasks.append(task)
                del self._waiting_tasks[i]
                self._error_total += 1
            else:
                i += 1

        i = 0
        while i < len(self._done_tasks):
            task = self._done_tasks[i]
            if len(self._done_tasks) > self._max_done_size:
                del self._done_tasks[i]
                self._logger.warning("保存的异步任务数超过了限制，删除前面的")
            elif task.is_timeout("done", self._max_done_timeout):
                # 如果等待太长时间没有来取，认为这个任务已经被抛弃，删除
                del self._done_tasks[i]
                self._logger.warning("删除超过时间还没有取走的异步任务")
            else:
                i += 1

    def _fetch_tasks(self):
        size: Final = self._max_running_size - len(self._running_tasks)
        if size <= 0:
            return

        tasks: Final[list[Task]] = []
        if not self._priorities:
            # 如果没有设置不同的优先级有不同的现在，就直接去前面n个
            # 实际上不执行判断，使用else结果也是一样的
            tasks.extend(self._waiting_tasks[0:size])
        else:
            # 按优先级选择
            running_counter = Counter([task.priority for task in self._running_tasks])
            for task in self._waiting_tasks:
                if len(tasks) >= size:
                    break
                # 获得优先级对应的最大任务数，如果没有定义，就默认为self._max_running_size
                # 也就是每个优先级都是一样的
                max_size = self._priorities.get(task.priority, self._max_running_size)
                current_size = running_counter[task.priority]
                if current_size < max_size:
                    running_counter.update([task.priority])
                    tasks.append(task)
                else:
                    # 该优先级的任务已经满了
                    pass
                pass

        for task in tasks:
            self._waiting_tasks.remove(task)
            self._running_tasks.append(task)
            task.start(self._max_running_timeout)
        pass

    def _gc_tasks(self):
        #因为task失败会保存异常，形成循环引用，需要等gc等时候才有可能释放
        #为了尽快释放，这里主动执行一次

        if self._gc_interval>0 and time.monotonic()-self._last_gc_clock>self._gc_interval and len(self._task_ids)>0:
            t1 = time.monotonic()
            gc.collect()
            self._last_gc_clock = time.monotonic()
            t2 = time.monotonic()
            self._logger.debug('gc elapsed=%s',t2-t1)
        else:
            pass

    def get_task(
        self, id: str, custom: bool = False, remove_if_done: bool = False
    ) -> Task | None:
        """得到任务，这个是给异步返回结果的时候使用的"""
        tasks_list = [self._waiting_tasks, self._running_tasks, self._done_tasks]
        for tasks in tasks_list:
            for i, task in enumerate(tasks):
                task_id = task.custom_id if custom else task.id
                if task_id == id:
                    if remove_if_done and task.done() and tasks is self._done_tasks:
                        #必须在done_task队列中
                        del tasks[i]
                    return task
        return None

    def add_task(self, task: Task):
        if self._stop_event.is_set():
            raise ApiError(ApiError.ANY, "系统已经关闭，无法添加任务")

        if len(
            self._waiting_tasks
        ) >= self._max_waiting_size + self._max_running_size - len(self._running_tasks):
            # if len(self._waiting_tasks) >= self._max_waiting_size:
            raise ApiError(ApiError.ANY, "系统繁忙，请稍后再试")

        if task.custom_id and self.get_task(task.custom_id, custom=True):
            raise ApiError(ApiError.ANY, f"已经存在相同的任务id:{task.custom_id}")
        # 这里还检查id吗？不需要检查了
        self._waiting_tasks.append(task)
        self._total += 1

        assert task.task_manager is None
        task.task_manager = self
        self._task_ids.append(task.id)
        weakref.finalize(task,self._on_gc_task,task.id)
    
    def _on_gc_task(self,id:str):
        self._task_ids.remove(id)

    def state(self) -> Any:
        # 定义北京时区 UTC+8
        CST = timezone(timedelta(hours=8))
        datetime.fromtimestamp(self._start_time, tz=CST).strftime("%Y-%m-%d %H:%M:%S")
        running_tasks: list[Any] = [task.state() for task in self._running_tasks]
        waiting_tasks: list[Any] = [task.state() for task in self._waiting_tasks]
        done_tasks:list[Any]=[task.state() for task in self._done_tasks]
        state = {
            # 毫秒
            "pid": os.getpid(),
            "start_time": int(self._start_time * 1000),
            "start_time_text": datetime.fromtimestamp(
                self._start_time, tz=CST
            ).strftime("%Y-%m-%d %H:%M:%S"),
            "uptime": self._timer.uptime(start="start"),
            'total':self._total,
            'success_total':self._success_total,
            'error_total':self._error_total,
            'max_running_size':self._max_running_size,
            'max_waiting_size':self._max_waiting_size,
            'max_done_size':self._max_done_size,
            'max_running_timeout':self._max_running_timeout,
            'max_waiting_timeout':self._max_waiting_timeout,
            'max_done_timeout':self._max_done_timeout,
            'running_size':len(running_tasks),
            'waiting_size':len(waiting_tasks),
            'done_size':len(done_tasks),
            "running_tasks": running_tasks,
            'waiting_tasks':waiting_tasks,
            'done_tasks':done_tasks
        }
        return state
