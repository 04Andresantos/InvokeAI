import traceback
from contextlib import suppress
from threading import BoundedSemaphore, Thread
from threading import Event as ThreadEvent
from typing import Callable, Optional, Union

from fastapi_events.handlers.local import local_handler
from fastapi_events.typing import Event as FastAPIEvent

from invokeai.app.invocations.baseinvocation import BaseInvocation
from invokeai.app.services.events.events_base import EventServiceBase
from invokeai.app.services.invocation_stats.invocation_stats_common import GESStatsNotFoundError
from invokeai.app.services.session_processor.session_processor_common import CanceledException
from invokeai.app.services.session_queue.session_queue_common import SessionQueueItem
from invokeai.app.services.shared.invocation_context import InvocationContextData, build_invocation_context
from invokeai.app.util.profiler import Profiler

from ..invoker import Invoker
from .session_processor_base import InvocationServices, SessionProcessorBase, SessionRunnerBase
from .session_processor_common import SessionProcessorStatus


class DefaultSessionRunner(SessionRunnerBase):
    """Processes a single session's invocations"""

    def __init__(
        self,
        on_before_run_node: Union[Callable[[BaseInvocation, SessionQueueItem], bool], None] = None,
        on_after_run_node: Union[Callable[[BaseInvocation, SessionQueueItem], bool], None] = None,
    ):
        self.on_before_run_node = on_before_run_node
        self.on_after_run_node = on_after_run_node

    def start(self, services: InvocationServices, cancel_event: ThreadEvent):
        """Start the session runner"""
        self.services = services
        self.cancel_event = cancel_event

    def next_invocation(
        self, previous_invocation: Optional[BaseInvocation], queue_item: SessionQueueItem, cancel_event: ThreadEvent
    ) -> Optional[BaseInvocation]:
        invocation = None
        if not (queue_item.session.is_complete() or cancel_event.is_set()):
            try:
                invocation = queue_item.session.next()
            except Exception as exc:
                self.services.logger.error("ERROR: %s" % exc, exc_info=True)

                node_error = str(exc)

                # Save error
                if previous_invocation is not None:
                    queue_item.session.set_node_error(previous_invocation.id, node_error)

                # Send error event
                self.services.events.emit_invocation_error(
                    queue_batch_id=queue_item.batch_id,
                    queue_item_id=queue_item.item_id,
                    queue_id=queue_item.queue_id,
                    graph_execution_state_id=queue_item.session.id,
                    node=previous_invocation.model_dump() if previous_invocation else {},
                    source_node_id=queue_item.session.prepared_source_mapping[previous_invocation.id]
                    if previous_invocation
                    else "",
                    error_type=exc.__class__.__name__,
                    error=node_error,
                    user_id=None,
                    project_id=None,
                )

        if queue_item.session.is_complete() or cancel_event.is_set():
            # Set the invocation to None to prepare for the next session
            invocation = None
        return invocation

    def run(self, queue_item: SessionQueueItem):
        """Run the graph"""
        if not queue_item.session:
            raise ValueError("Queue item has no session")
        invocation = None
        # Loop over invocations until the session is complete or canceled
        while self.next_invocation(invocation, queue_item, self.cancel_event) and not self.cancel_event.is_set():
            # Prepare the next node
            invocation = queue_item.session.next()
            if invocation is None:
                # If there are no more invocations, complete the graph
                break
            # Build invocation context (the node-facing API
            self.run_node(invocation.id, queue_item)
        self.complete(queue_item)

    def complete(self, queue_item: SessionQueueItem):
        # Send complete event
        self.services.events.emit_graph_execution_complete(
            queue_batch_id=queue_item.batch_id,
            queue_item_id=queue_item.item_id,
            queue_id=queue_item.queue_id,
            graph_execution_state_id=queue_item.session.id,
        )
        # We'll get a GESStatsNotFoundError if we try to log stats for an untracked graph, but in the processor
        # we don't care about that - suppress the error.
        with suppress(GESStatsNotFoundError):
            self.services.performance_statistics.log_stats(queue_item.session.id)
            self.services.performance_statistics.reset_stats()

    def _on_before_run_node(self, invocation: BaseInvocation, queue_item: SessionQueueItem):
        """Run before a node is executed"""
        # Send starting event
        self.services.events.emit_invocation_started(
            queue_batch_id=queue_item.batch_id,
            queue_item_id=queue_item.item_id,
            queue_id=queue_item.queue_id,
            graph_execution_state_id=queue_item.session_id,
            node=invocation.model_dump(),
            source_node_id=queue_item.session.prepared_source_mapping[invocation.id],
        )
        if self.on_before_run_node is not None:
            self.on_before_run_node(invocation, queue_item)

    def _on_after_run_node(self, invocation: BaseInvocation, queue_item: SessionQueueItem):
        """Run after a node is executed"""
        if self.on_after_run_node is not None:
            self.on_after_run_node(invocation, queue_item)

    def run_node(self, node_id: str, queue_item: SessionQueueItem):
        """Run a single node in the graph"""
        # If this error raises a NodeNotFoundError that's handled by the processor
        invocation = queue_item.session.execution_graph.get_node(node_id)
        try:
            self._on_before_run_node(invocation, queue_item)
            data = InvocationContextData(
                invocation=invocation,
                source_invocation_id=queue_item.session.prepared_source_mapping[invocation.id],
                queue_item=queue_item,
            )

            # Innermost processor try block; any unhandled exception is an invocation error & will fail the graph
            with self.services.performance_statistics.collect_stats(invocation, queue_item.session_id):
                context = build_invocation_context(
                    data=data,
                    services=self.services,
                    cancel_event=self.cancel_event,
                )

                # Invoke the node
                outputs = invocation.invoke_internal(context=context, services=self.services)

                # Save outputs and history
                queue_item.session.complete(invocation.id, outputs)

            self._on_after_run_node(invocation, queue_item)
            # Send complete event on successful runs
            self.services.events.emit_invocation_complete(
                queue_batch_id=queue_item.batch_id,
                queue_item_id=queue_item.item_id,
                queue_id=queue_item.queue_id,
                graph_execution_state_id=queue_item.session.id,
                node=invocation.model_dump(),
                source_node_id=data.source_invocation_id,
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
            queue_item.session.set_node_error(invocation.id, error)
            self.services.logger.error(
                f"Error while invoking session {queue_item.session_id}, invocation {invocation.id} ({invocation.get_type()}):\n{e}"
            )
            self.services.logger.error(error)

            # Send error event
            self.services.events.emit_invocation_error(
                queue_batch_id=queue_item.session_id,
                queue_item_id=queue_item.item_id,
                queue_id=queue_item.queue_id,
                graph_execution_state_id=queue_item.session.id,
                node=invocation.model_dump(),
                source_node_id=queue_item.session.prepared_source_mapping[invocation.id],
                error_type=e.__class__.__name__,
                error=error,
                user_id=None,
                project_id=None,
            )


class DefaultSessionProcessor(SessionProcessorBase):
    def __init__(self, session_runner: Union[SessionRunnerBase, None] = None) -> None:
        super().__init__()
        self.session_runner = session_runner if session_runner else DefaultSessionRunner()

    def start(
        self,
        invoker: Invoker,
        thread_limit: int = 1,
        polling_interval: int = 1,
        on_before_run_session: Union[Callable[[SessionQueueItem], bool], None] = None,
        on_after_run_session: Union[Callable[[SessionQueueItem], bool], None] = None,
    ) -> None:
        self._invoker: Invoker = invoker
        self._queue_item: Optional[SessionQueueItem] = None
        self._invocation: Optional[BaseInvocation] = None
        self.on_before_run_session = on_before_run_session
        self.on_after_run_session = on_after_run_session

        self._resume_event = ThreadEvent()
        self._stop_event = ThreadEvent()
        self._poll_now_event = ThreadEvent()
        self._cancel_event = ThreadEvent()

        local_handler.register(event_name=EventServiceBase.queue_event, _func=self._on_queue_event)

        self._thread_limit = thread_limit
        self._thread_semaphore = BoundedSemaphore(thread_limit)
        self._polling_interval = polling_interval

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

        self.session_runner.start(services=invoker.services, cancel_event=self._cancel_event)
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

    def stop(self, *args, **kwargs) -> None:
        self._stop_event.set()

    def _poll_now(self) -> None:
        self._poll_now_event.set()

    async def _on_queue_event(self, event: FastAPIEvent) -> None:
        event_name = event[1]["event"]

        if (
            event_name == "session_canceled"
            and self._queue_item
            and self._queue_item.item_id == event[1]["data"]["queue_item_id"]
        ):
            self._cancel_event.set()
            self._poll_now()
        elif (
            event_name == "queue_cleared"
            and self._queue_item
            and self._queue_item.queue_id == event[1]["data"]["queue_id"]
        ):
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
            is_processing=self._queue_item is not None,
        )

    def _process(
        self,
        stop_event: ThreadEvent,
        poll_now_event: ThreadEvent,
        resume_event: ThreadEvent,
        cancel_event: ThreadEvent,
    ):
        # Outermost processor try block; any unhandled exception is a fatal processor error
        try:
            self._thread_semaphore.acquire()
            stop_event.clear()
            resume_event.set()
            cancel_event.clear()

            while not stop_event.is_set():
                poll_now_event.clear()
                # Middle processor try block; any unhandled exception is a non-fatal processor error
                try:
                    # If we are paused, wait for resume event
                    resume_event.wait()

                    # Get the next session to process
                    self._queue_item = self._invoker.services.session_queue.dequeue()

                    if self._queue_item is None:
                        # The queue was empty, wait for next polling interval or event to try again
                        self._invoker.services.logger.debug("Waiting for next polling interval or event")
                        poll_now_event.wait(self._polling_interval)
                        continue

                    self._invoker.services.logger.debug(f"Executing queue item {self._queue_item.item_id}")
                    cancel_event.clear()

                    # If we have a on_before_run_session callback, call it
                    if self.on_before_run_session is not None:
                        self.on_before_run_session(self._queue_item)

                    # If profiling is enabled, start the profiler
                    if self._profiler is not None:
                        self._profiler.start(profile_id=self._queue_item.session_id)

                    # Run the graph
                    self.session_runner.run(queue_item=self._queue_item)

                    # If we are profiling, stop the profiler and dump the profile & stats
                    if self._profiler:
                        profile_path = self._profiler.stop()
                        stats_path = profile_path.with_suffix(".json")
                        self._invoker.services.performance_statistics.dump_stats(
                            graph_execution_state_id=self._queue_item.session.id, output_path=stats_path
                        )

                except Exception:
                    # Non-fatal error in processor
                    self._invoker.services.logger.error(
                        f"Non-fatal error in session processor:\n{traceback.format_exc()}"
                    )
                    # Cancel the queue item
                    if self._queue_item is not None:
                        self._invoker.services.session_queue.set_queue_item_session(
                            self._queue_item.item_id, self._queue_item.session
                        )
                        self._invoker.services.session_queue.cancel_queue_item(
                            self._queue_item.item_id, error=traceback.format_exc()
                        )
                    # Reset the invocation to None to prepare for the next session
                    self._invocation = None
                    # Immediately poll for next queue item
                    poll_now_event.wait(self._polling_interval)
                    continue
        except Exception:
            # Fatal error in processor, log and pass - we're done here
            self._invoker.services.logger.error(f"Fatal Error in session processor:\n{traceback.format_exc()}")
            pass
        finally:
            stop_event.clear()
            poll_now_event.clear()
            self._queue_item = None
            self._thread_semaphore.release()
