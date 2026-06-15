"""QoI relative error computation."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def compute_qoi_errors(
    reference: dict[str, float],
    computed: dict[str, float],
) -> dict[str, float]:
    """Compute relative errors for each QoI.

    Relative error = |computed - reference| / |reference|

    Only QoIs present in both reference and computed are included.
    Reference values of 0 are skipped (division by zero) and logged.

    Args:
        reference: Reference QoI values.
        computed: Computed QoI values.

    Returns:
        Dict mapping QoI name to relative error.
    """
    errors: dict[str, float] = {}
    for key in reference:
        if key not in computed:
            continue
        ref_val = reference[key]
        if ref_val == 0:
            logger.warning("reference value for '%s' is 0, skipping relative error", key)
            continue
        errors[key] = abs(computed[key] - ref_val) / abs(ref_val)
    return errors
