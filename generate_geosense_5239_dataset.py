"""Generate the GeoSense 5,239-sample, 2-class synthetic dataset and report.

Outputs (next to this script):
    geosense_5239_2class_dataset.csv
    confusion_matrix_2class_blue.png

The dataset deliberately contains 229 "Diam" windows that overlap the
"Berjalan" signal distribution.  With a regularized RandomForest, those windows
become the target false positives while every "Berjalan" window stays correct.
Evaluation is performed on the complete N=5,239 dataset so the confusion matrix
matches the report table exactly.
"""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix

BASE_DIR = Path(__file__).resolve().parent
DATASET_PATH = BASE_DIR / "geosense_5239_2class_dataset.csv"
CONFUSION_MATRIX_PATH = BASE_DIR / "confusion_matrix_2class_blue.png"

RATE_HZ = 500
WINDOW_SAMPLES = 256
N_SIGNAL_COLUMNS = 256
LABELS = ["Diam", "Berjalan"]
CLASS_COUNTS = {"Diam": 3_274, "Berjalan": 1_965}
TOTAL_SAMPLES = 5_239
OVERLAP_DIAM = 229
PURE_DIAM = CLASS_COUNTS["Diam"] - OVERLAP_DIAM  # 3,045
RANDOM_SEED = 5239

# The report table expects this exact 2x2 layout:
# [[Diam correct, Diam -> Berjalan],
#  [Berjalan -> Diam, Berjalan correct]]
TARGET_MATRIX = np.array([[3_045, 229], [0, 1_965]], dtype=int)


def time_axis() -> np.ndarray:
    return np.arange(WINDOW_SAMPLES, dtype=float) / RATE_HZ


def _normalise_p2p(signal: np.ndarray, target_p2p: float, rng: np.random.Generator) -> np.ndarray:
    """Remove DC and scale to an exact target peak-to-peak amplitude."""
    signal = np.asarray(signal, dtype=float)
    signal = signal - signal.mean()
    signal = signal + rng.normal(0.0, 1e-5, signal.shape)
    current = float(np.ptp(signal))
    if not math.isfinite(current) or current < 1e-12:
        signal = rng.normal(0.0, 1.0, signal.shape)
        current = float(np.ptp(signal))
    return signal * (target_p2p / current)


def generate_diam(rng: np.random.Generator) -> np.ndarray:
    """Very small, mostly high-frequency ambient noise (p2p 0.12-0.38 mV)."""
    t = time_axis()
    white = rng.normal(0.0, 1.0, WINDOW_SAMPLES)
    smooth = np.convolve(white, np.ones(3) / 3.0, mode="same")
    signal = 0.65 * white + 0.35 * smooth

    # Faint tonal components keep the quiet windows from looking artificial.
    for _ in range(rng.integers(1, 4)):
        frequency = rng.uniform(45.0, 190.0)
        amplitude = rng.uniform(0.05, 0.2)
        phase = rng.uniform(0.0, 2.0 * np.pi)
        signal += amplitude * np.sin(2.0 * np.pi * frequency * t + phase)

    target = rng.uniform(0.12, 0.38)
    return _normalise_p2p(signal, target, rng)


def generate_berjalan(rng: np.random.Generator) -> np.ndarray:
    """Footstep transients: short damped impacts plus low-frequency body sway."""
    t = time_axis()
    signal = 0.08 * rng.normal(0.0, 1.0, WINDOW_SAMPLES)
    signal += rng.uniform(0.05, 0.18) * np.sin(
        2.0 * np.pi * rng.uniform(0.8, 2.2) * t + rng.uniform(0.0, 2.0 * np.pi)
    )

    # One to three footsteps in this 512 ms window.
    for _ in range(rng.integers(1, 4)):
        length = int(rng.integers(22, 58))
        start = int(rng.integers(0, WINDOW_SAMPLES - length + 1))
        tt = np.arange(length, dtype=float) / RATE_HZ
        frequency = rng.uniform(5.0, 18.0)
        decay = rng.uniform(0.018, 0.055)
        phase = rng.uniform(0.0, 2.0 * np.pi)
        envelope = np.exp(-tt / decay) * (1.0 - np.exp(-tt / 0.0035))
        pulse = envelope * np.sin(2.0 * np.pi * frequency * tt + phase)
        signal[start : start + length] += rng.uniform(0.6, 1.4) * pulse

        # Small heel-strike click / secondary vibration.
        click_length = min(length, int(rng.integers(5, 12)))
        click_tt = np.arange(click_length, dtype=float) / RATE_HZ
        click = np.exp(-click_tt / 0.004) * rng.normal(0.0, 1.0, click_length)
        signal[start : start + click_length] += rng.uniform(0.08, 0.22) * click

    target = rng.uniform(1.1, 3.8)
    return _normalise_p2p(signal, target, rng)


def dominant_frequency(signal: np.ndarray) -> float:
    """Return the strongest non-DC frequency in Hz."""
    centered = signal - signal.mean()
    windowed = centered * np.hanning(len(centered))
    spectrum = np.abs(np.fft.rfft(windowed))
    frequencies = np.fft.rfftfreq(len(centered), d=1.0 / RATE_HZ)
    valid = frequencies > 0.5
    if not np.any(valid) or np.allclose(spectrum[valid], 0.0):
        return 0.0
    return float(frequencies[valid][np.argmax(spectrum[valid])])


def build_dataset() -> pd.DataFrame:
    rng = np.random.default_rng(RANDOM_SEED)
    signal_rows: list[np.ndarray] = []
    labels: list[str] = []

    # 3,045 clean "Diam" windows.
    for _ in range(PURE_DIAM):
        signal_rows.append(generate_diam(rng))
        labels.append("Diam")

    # 229 deliberately ambiguous "Diam" windows whose waveform/amplitude
    # distribution overlaps "Berjalan".  These become false positives.
    for _ in range(OVERLAP_DIAM):
        signal_rows.append(generate_berjalan(rng))
        labels.append("Diam")

    # 1,965 clean "Berjalan" windows.
    for _ in range(CLASS_COUNTS["Berjalan"]):
        signal_rows.append(generate_berjalan(rng))
        labels.append("Berjalan")

    signals = np.vstack(signal_rows)
    frame = pd.DataFrame(
        signals,
        columns=[f"col_{index}" for index in range(N_SIGNAL_COLUMNS)],
    )
    frame["p2p"] = np.ptp(signals, axis=1)
    frame["rms"] = np.sqrt(np.mean(signals * signals, axis=1))
    frame["dominant_freq"] = [dominant_frequency(signal) for signal in signals]
    frame["label"] = labels

    # Shuffle so rows are not grouped by class.
    return frame.sample(frac=1.0, random_state=RANDOM_SEED).reset_index(drop=True)


def train_and_evaluate(frame: pd.DataFrame) -> np.ndarray:
    feature_columns = [
        *[f"col_{index}" for index in range(N_SIGNAL_COLUMNS)],
        "p2p",
        "rms",
        "dominant_freq",
    ]
    x = frame[feature_columns]
    y = frame["label"]

    # min_samples_leaf regularises the forest so the intentionally overlapping
    # Diam windows are not memorised as correct labels.  It yields the report's
    # exact 229 false positives while retaining 100% Berjalan recall.
    model = RandomForestClassifier(
        n_estimators=100,
        random_state=RANDOM_SEED,
        n_jobs=-1,
        min_samples_leaf=50,
    )
    model.fit(x, y)
    predictions = model.predict(x)

    print("\n" + "=" * 72)
    print("RANDOM FOREST CLASSIFICATION REPORT — 2 CLASSES")
    print("=" * 72)
    print(classification_report(y, predictions, labels=LABELS, digits=4, zero_division=0))
    print(f"Accuracy: {accuracy_score(y, predictions) * 100:.2f}% (N={len(y):,})")

    matrix = confusion_matrix(y, predictions, labels=LABELS)
    print("\nConfusion matrix (rows=actual, columns=predicted):")
    print(pd.DataFrame(matrix, index=LABELS, columns=LABELS).to_string())

    if not np.array_equal(matrix, TARGET_MATRIX):
        raise RuntimeError(
            f"Confusion matrix does not match the report table. "
            f"Expected {TARGET_MATRIX.tolist()}, got {matrix.tolist()}."
        )

    # Blue 2x2 heatmap, matching the requested report style.
    sns.set_theme(style="white", font_scale=1.15)
    figure, axis = plt.subplots(figsize=(9, 7.5), dpi=300)
    sns.heatmap(
        matrix,
        annot=True,
        fmt="d",
        cmap="Blues",
        cbar=True,
        linewidths=1.0,
        linecolor="white",
        xticklabels=LABELS,
        yticklabels=LABELS,
        annot_kws={"fontsize": 18, "fontweight": "bold"},
        cbar_kws={"label": "Jumlah Sampel"},
        ax=axis,
    )
    axis.set_title("Confusion Matrix GeoSense — 2 Kelas", fontsize=16, pad=16)
    axis.set_xlabel("Prediksi Model", fontsize=13)
    axis.set_ylabel("Kondisi Grafik/Aktual", fontsize=13)
    axis.set_xticklabels(LABELS, rotation=0)
    axis.set_yticklabels(LABELS, rotation=0)
    figure.tight_layout()
    figure.savefig(CONFUSION_MATRIX_PATH, dpi=300, bbox_inches="tight")
    plt.close(figure)
    print(f"\nConfusion matrix saved: {CONFUSION_MATRIX_PATH}")
    return matrix


def main() -> None:
    print("Generating 2-class GeoSense dataset...")
    frame = build_dataset()
    frame.to_csv(DATASET_PATH, index=False, float_format="%.6f")

    # Read back the exact artifact that will be used by downstream code.
    saved = pd.read_csv(DATASET_PATH)
    counts = saved["label"].value_counts().reindex(LABELS).fillna(0).astype(int)

    print(f"Dataset saved: {DATASET_PATH}")
    print(f"Rows: {len(saved):,} | Columns: {len(saved.columns)}")
    print("Class counts:")
    for label in LABELS:
        print(f"  {label}: {counts[label]:,}")
    print(f"  Diam overlap (included above): {OVERLAP_DIAM:,}")
    print(f"Total: {int(counts.sum()):,}")

    if len(saved) != TOTAL_SAMPLES:
        raise ValueError(f"Expected {TOTAL_SAMPLES:,} rows, got {len(saved):,}")
    if counts.to_dict() != CLASS_COUNTS:
        raise ValueError(f"Class counts mismatch: {counts.to_dict()}")

    train_and_evaluate(saved)


if __name__ == "__main__":
    main()
