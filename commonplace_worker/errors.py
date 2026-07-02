"""Shared worker exception types.

``RetryableHandlerError`` signals to the worker that a job failed due
to a transient condition (network blip, upstream 5xx, Whisper OOM
from a concurrent process, etc.) and should be re-queued under the
*same* ``job_id`` rather than permanently marked ``failed``. Preserving
the ``job_id`` is the mechanism by which stage-level checkpointing
delivers value — a re-claimed job reads its prior checkpoints and
resumes from the last complete stage.

Non-retryable failures (malformed inputs, permission denied, assertion
failures, etc.) keep raising their native exception types and flow
through the existing ``_mark_failed`` path, where the poison-pill guard
still catches genuinely broken jobs.
"""

from __future__ import annotations


class RetryableHandlerError(Exception):
    """Transient handler failure; re-queue under same job_id.

    Handlers raise this to signal 'try again later, same job.' The
    worker re-queues the job (decrementing attempts so the transient
    blip doesn't eat into ``COMMONPLACE_MAX_ATTEMPTS``). After
    ``COMMONPLACE_MAX_ATTEMPTS`` retries the worker promotes the
    failure to a regular ``_mark_failed`` so a systematic issue still
    lands in the operator's failed-job report eventually.
    """
