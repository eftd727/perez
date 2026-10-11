from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from math import isfinite

MAX_RISK_FRACTION = 0.0025


@dataclass(frozen=True)
class Instrument:
    tick_size: float
    tick_value: float
    volume_min: float
    volume_max: float
    volume_step: float


def position_size(
    equity: float,
    stop_distance: float,
    instrument: Instrument,
    risk_fraction: float = MAX_RISK_FRACTION,
    estimated_cost_per_lot: float = 0.0,
) -> float:
    """Return a volume rounded down so estimated stop risk stays within the cap."""
    values = (equity, stop_distance, instrument.tick_size, instrument.tick_value,
              instrument.volume_min, instrument.volume_max, instrument.volume_step,
              risk_fraction, estimated_cost_per_lot)
    if not all(isfinite(value) for value in values):
        return 0.0
    if (equity <= 0 or stop_distance <= 0 or instrument.tick_size <= 0 or instrument.tick_value <= 0
            or instrument.volume_step <= 0 or instrument.volume_min <= 0
            or instrument.volume_max < instrument.volume_min):
        return 0.0
    fraction = min(max(risk_fraction, 0.0), MAX_RISK_FRACTION)
    loss_per_lot = (stop_distance / instrument.tick_size * instrument.tick_value
                    + max(estimated_cost_per_lot, 0.0))
    raw_volume = Decimal(str(equity * fraction / loss_per_lot))
    minimum = Decimal(str(instrument.volume_min))
    maximum = Decimal(str(instrument.volume_max))
    step = Decimal(str(instrument.volume_step))
    if raw_volume < minimum:
        return 0.0
    steps = ((raw_volume - minimum) / step).to_integral_value(rounding=ROUND_FLOOR)
    volume = minimum + steps * step
    max_steps = ((maximum - minimum) / step).to_integral_value(rounding=ROUND_FLOOR)
    maximum_aligned = minimum + max_steps * step
    return float(min(volume, maximum_aligned))