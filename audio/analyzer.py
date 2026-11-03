import numpy as np

MIN_DB = -80.0
BLOCK_FRAMES = 20000  # メモリを増やさないよう、この単位で計算する


def compute_levels(
    audio: np.ndarray,
    sr: int,
    window_ms: float = 50.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Compute RMS and peak dB level for each window.

    Returns:
        times: array of center times in seconds
        rms_db: RMS of the mono mix, clipped to [MIN_DB, 0]
        peak_db: peak over all channels, not clipped above 0
    """
    hop = max(int(sr * window_ms / 1000.0), 1)
    n_samples = audio.shape[0]
    starts = np.arange(0, n_samples, hop)

    rms = np.zeros(len(starts), dtype=np.float32)
    peak = np.zeros(len(starts), dtype=np.float32)
    for first in range(0, len(starts), BLOCK_FRAMES):
        block_starts = starts[first:first + BLOCK_FRAMES]
        block = audio[block_starts[0]:block_starts[-1] + hop]
        edges = block_starts - block_starts[0]
        lengths = np.diff(np.append(edges, len(block)))

        mono = block.mean(axis=1)
        energy = np.add.reduceat(np.square(mono, dtype=np.float64), edges)
        rms[first:first + len(edges)] = np.sqrt(energy / lengths)
        peak[first:first + len(edges)] = np.maximum.reduceat(np.abs(block).max(axis=1), edges)

    times = np.minimum(starts + hop / 2.0, n_samples) / sr
    return times, np.minimum(_to_db(rms), 0.0), _to_db(peak)


def _to_db(values: np.ndarray) -> np.ndarray:
    floor = 10.0 ** (MIN_DB / 20.0)
    return (20.0 * np.log10(np.maximum(values, floor))).astype(np.float32)
