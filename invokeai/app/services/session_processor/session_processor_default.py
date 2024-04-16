import traceback
from contextlib import suppress
from queue import Queue
from threading import BoundedSemaphore, Lock, Thread
from threading import Event as ThreadEvent
from typing import Optional, Set

from fastapi_events.handlers.local import local_handler
from fastapi_events.typing import Event as FastAPIEvent

from invokeai.app.invocations.baseinvocation import BaseInvocation
from invokeai.app.services.events.events_base import EventServiceBase
from invokeai.app.services.invocation_stats.invocation_stats_common import GESStatsNotFoundError
from invokeai.app.services.invocation_stats.invocation_stats_default import InvocationStatsService
from invokeai.app.services.session_processor.session_processor_common import CanceledException
from invokeai.app.services.session_queue.session_queue_common import SessionQueueItem
from invokeai.app.services.shared.invocation_context import InvocationContextData, build_invocation_context
from invokeai.app.util.profiler import Profiler

from ..invoker import Invoker
from .session_processor_base import SessionProcessorBase
from .session_processor_common import SessionProcessorStatus


class DefaultSessionProcessor(SessionProcessorBase):
    def start(self, invoker: Invoker, polling_interval: int = 1) -> None:
        self._invoker: Invoker = invoker
        self._queue_items: Set[int] = set()
        self._sessions_to_cancel: Set[int] = set()
        self._invocation: Optional[BaseInvocation] = None

        self._resume_event = ThreadEvent()
        self._stop_event = ThreadEvent()
        self._poll_now_event = ThreadEvent()
        self._cancel_event = ThreadEvent()

        local_handler.register(event_name=EventServiceBase.queue_event, _func=self._on_queue_event)

        self._thread_limit = 1
        self._thread_semaphore = BoundedSemaphore(self._thread_limit)
        self._polling_interval = polling_interval

        self._worker_thread_count = self._invoker.services.configuration.max_threads
        self._session_worker_queue: Queue[SessionQueueItem] = Queue()
        self._process_lock = Lock()

        # If profiling is enabled, create a profiler. The same profiler will be used for all sessions. Internally,
        # the profiler will create a new profile for each session.
        self._profiler = (
            Profiler(
                logger=self._invoker.services.logger,
                output_dir=self._invoker.services.configuration.profiles_path,
                prefix=self._invoker.services.configuration.profile_prefix,
            )
            if self._invoker.services.configuration.profile_graphs
            else None
        )

        # main session processor loop - single thread
        self._thread = Thread(
            name="session_processor",
            target=self._process,
            kwargs={
                "stop_event": self._stop_event,
                "poll_now_event": self._poll_now_event,
                "resume_event": self._resume_event,
                "cancel_event": self._cancel_event,
            },
        )
        self._thread.start()

        # Session processor workers - multithreaded
        self._invoker.services.logger.debug(f"Starting {self._worker_thread_count} session processing threads.")
        for _i in range(0, self._worker_thread_count):
            worker = Thread(
                name="session_worker",
                target=self._process_next_session,
                daemon=True,
            )
            worker.start()

    def stop(self, *args, **kwargs) -> None:
        self._stop_event.set()

    def _poll_now(self) -> None:
        self._poll_now_event.set()

    async def _on_queue_event(self, event: FastAPIEvent) -> None:
        event_name = event[1]["event"]

        if event_name == "session_canceled" and event[1]["data"]["queue_item_id"] in self._queue_items:
            self._sessions_to_cancel.add(event[1]["data"]["queue_item_id"])
            self._cancel_event.set()
            self._poll_now()
        elif event_name == "queue_cleared" and event[1]["data"]["queue_id"] in self._queue_items:
            self._sessions_to_cancel.add(event[1]["data"]["queue_item_id"])
            self._cancel_event.set()
            self._poll_now()
        elif event_name == "batch_enqueued":
            self._poll_now()
        elif event_name == "queue_item_status_changed" and event[1]["data"]["queue_item"]["status"] in [
            "completed",
            "failed",
            "canceled",
        ]:
            self._poll_now()

    def resume(self) -> SessionProcessorStatus:
        if not self._resume_event.is_set():
            self._resume_event.set()
        return self.get_status()

    def pause(self) -> SessionProcessorStatus:
        if self._resume_event.is_set():
            self._resume_event.clear()
        return self.get_status()

    def get_status(self) -> SessionProcessorStatus:
        return SessionProcessorStatus(
            is_started=self._resume_event.is_set(),
            is_processing=len(self._queue_items) > 0,
        )

    def _process(
        self,
        stop_event: ThreadEvent,
        poll_now_event: ThreadEvent,
        resume_event: ThreadEvent,
        cancel_event: ThreadEvent,
    ) -> None:
        # Outermost processor try block; any unhandled exception is a fatal processor error
        try:
            self._thread_semaphore.acquire()
            stop_event.clear()
            resume_event.set()
            cancel_event.clear()

            while not stop_event.is_set():
                poll_now_event.clear()
                resume_event.wait()

                # Get the next session to process
                session = self._invoker.services.session_queue.dequeue()

                if session is None:
                    # The queue was empty, wait for next polling interval or event to try again
                    self._invoker.services.logger.debug("Waiting for next polling interval or event")
                    poll_now_event.wait(self._polling_interval)
                    continue

                self._queue_items.add(session.item_id)
                self._session_worker_queue.put(session)
                self._invoker.services.logger.debug(f"Executing queue item {session.item_id}")
                cancel_event.clear()
        except Exception:
            # Fatal error in processor, log and pass - we're done here
            self._invoker.services.logger.error(f"Fatal Error in session processor:\n{traceback.format_exc()}")
            pass
        finally:
            stop_event.clear()
            poll_now_event.clear()
            self._queue_items.clear()
            self._thread_semaphore.release()

    def _process_next_session(self) -> None:
        profiler = (
            Profiler(
                logger=self._invoker.services.logger,
                output_dir=self._invoker.services.configuration.profiles_path,
                prefix=self._invoker.services.configuration.profile_prefix,
            )
            if self._invoker.services.configuration.profile_graphs
            else None
        )
        stats_service = InvocationStatsService()
        stats_service.start(self._invoker)

        while True:
            # Outer try block. Any error here is a fatal processor error
            try:
                session = self._session_worker_queue.get()
                if self._cancel_event.is_set():
                    if session.item_id in self._sessions_to_cancel:
                        continue

                if profiler is not None:
                    profiler.start(profile_id=session.session_id)

                # reserve a GPU for this session - may block
                with self._invoker.services.model_manager.load.ram_cache.reserve_execution_device() as gpu:

                    # Prepare invocations and take the first
                    with self._process_lock:
                        invocation = session.session.next()

                    # Loop over invocations until the session is complete or canceled
                    while invocation is not None and not self._cancel_event.is_set():
                        self._process_next_invocation(session, invocation, stats_service)

                        # The session is complete if all invocations are complete or there was an error
                        if session.session.is_complete():
                            # Send complete event
                            self._invoker.services.events.emit_graph_execution_complete(
                                queue_batch_id=session.batch_id,
                                queue_item_id=session.item_id,
                                queue_id=session.queue_id,
                                graph_execution_state_id=session.session.id,
                            )
                            # Log stats
                            # We'll get a GESStatsNotFoundError if we try to log stats for an untracked graph, but in the processor
                            # we don't care about that - suppress the error.
                            with suppress(GESStatsNotFoundError):
                                stats_service.log_stats(session.session.id)
                                stats_service.reset_stats()

                            # If we are profiling, stop the profiler and dump the profile & stats
                            if self._profiler:
                                profile_path = self._profiler.stop()
                                stats_path = profile_path.with_suffix(".json")
                                stats_service.dump_stats(
                                    graph_execution_state_id=session.session.id, output_path=stats_path
                                )
                            self._queue_items.remove(session.item_id)
                            invocation = None
                        else:
                            # Prepare the next invocation
                            with self._process_lock:
                                invocation = session.session.next()

            except Exception:
                # Non-fatal error in processor
                self._invoker.services.logger.error(f"Non-fatal error in session processor:\n{traceback.format_exc()}")

                # Cancel the queue item
                if session is not None:
                    self._invoker.services.session_queue.cancel_queue_item(
                        session.item_id, error=traceback.format_exc()
                    )
            finally:
                self._session_worker_queue.task_done()

    def _process_next_invocation(
        self,
        session: SessionQueueItem,
        invocation: BaseInvocation,
        stats_service: InvocationStatsService,
    ) -> None:
        # get the source node id to provide to clients (the prepared node id is not as useful)
        source_invocation_id = session.session.prepared_source_mapping[invocation.id]

        self._invoker.services.logger.debug(f"Executing invocation {session.session.id}:{source_invocation_id}")

        # Send starting event
        self._invoker.services.events.emit_invocation_started(
            queue_batch_id=session.batch_id,
            queue_item_id=session.item_id,
            queue_id=session.queue_id,
            graph_execution_state_id=session.session_id,
            node=invocation.model_dump(),
            source_node_id=source_invocation_id,
        )

        # Innermost processor try block; any unhandled exception is an invocation error & will fail the graph
        try:
            # Build invocation context (the node-facing API)
            data = InvocationContextData(
                invocation=invocation,
                source_invocation_id=source_invocation_id,
                queue_item=session,
            )
            context = build_invocation_context(
                data=data,
                services=self._invoker.services,
                cancel_event=self._cancel_event,
            )

            # Invoke the node
            # title = invocation.UIConfig.title
            with stats_service.collect_stats(invocation, session.session.id):
                outputs = invocation.invoke_internal(context=context, services=self._invoker.services)

            # Save outputs and history
            session.session.complete(invocation.id, outputs)

            # Send complete event
            self._invoker.services.events.emit_invocation_complete(
                queue_batch_id=session.batch_id,
                queue_item_id=session.item_id,
                queue_id=session.queue_id,
                graph_execution_state_id=session.session.id,
                node=invocation.model_dump(),
                source_node_id=source_invocation_id,
                result=outputs.model_dump(),
            )

        except KeyboardInterrupt:
            # TODO(MM2): Create an event for this
            pass

        except CanceledException:
            # When the user cancels the graph, we first set the cancel event. The event is checked
            # between invocations, in this loop. Some invocations are long-running, and we need to
            # be able to cancel them mid-execution.
            #
            # For example, denoising is a long-running invocation with many steps. A step callback
            # is executed after each step. This step callback checks if the canceled event is set,
            # then raises a CanceledException to stop execution immediately.
            #
            # When we get a CanceledException, we don't need to do anything - just pass and let the
            # loop go to its next iteration, and the cancel event will be handled correctly.
            pass

        except Exception as e:
            error = traceback.format_exc()

            # Save error
            session.session.set_node_error(invocation.id, error)
            self._invoker.services.logger.error(
                f"Error while invoking session {session.session_id}, invocation {invocation.id} ({invocation.get_type()}):\n{e}"
            )
            self._invoker.services.logger.error(error)

            # Send error event
            self._invoker.services.events.emit_invocation_error(
                queue_batch_id=session.session_id,
                queue_item_id=session.item_id,
                queue_id=session.queue_id,
                graph_execution_state_id=session.session.id,
                node=invocation.model_dump(),
                source_node_id=source_invocation_id,
                error_type=e.__class__.__name__,
                error=error,
            )
