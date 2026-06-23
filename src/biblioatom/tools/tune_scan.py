"""CLI-утилита для автоматического подбора оптимальных констант ScanExtractionSettings.

Поддерживает два режима:
  - grid   : случайный перебор по заданной сетке (без доп. зависимостей)
  - optuna : байесовская оптимизация через Optuna (требует ``uv add optuna``)

Метрика: средний F1 по IoU-порогу, IoU как вторичная метрика.

Формат ground-truth JSON::

    [
      {"image": "page_001.png", "boxes": [[x, y, w, h], ...]},
      ...
    ]

Запуск::

    uv run python -m biblioatom.tools.tune_scan tune \\
        --images ./tests/fixtures/scans \\
        --gt     ./tests/fixtures/gt.json \\
        --mode   optuna --trials 50
"""

from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from biblioatom.config import ScanExtractionSettings
from biblioatom.services.scan_extractor import ScanExtractor

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

app = typer.Typer(name="tune-scan", add_completion=False)

# ---------------------------------------------------------------------------
# Типы
# ---------------------------------------------------------------------------

BBox = tuple[int, int, int, int]  # x, y, w, h
GroundTruth = dict[str, list[BBox]]  # filename → list[bbox]

# ---------------------------------------------------------------------------
# Метрика
# ---------------------------------------------------------------------------


def _iou(a: BBox, b: BBox) -> float:
    """IoU двух прямоугольников (x, y, w, h)."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(ax, bx)
    iy = max(ay, by)
    iw = max(0, min(ax + aw, bx + bw) - ix)
    ih = max(0, min(ay + ah, by + bh) - iy)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _precision_recall_f1(
    pred: list[BBox],
    gt: list[BBox],
    iou_threshold: float = 0.5,
) -> tuple[float, float, float]:
    """Precision / Recall / F1 для одного изображения."""
    if not gt and not pred:
        return 1.0, 1.0, 1.0
    if not pred:
        return 0.0, 0.0, 0.0
    if not gt:
        return 0.0, 0.0, 0.0

    matched_gt: set[int] = set()
    tp = 0
    for p in pred:
        best_iou = 0.0
        best_j = -1
        for j, g in enumerate(gt):
            if j in matched_gt:
                continue
            v = _iou(p, g)
            if v > best_iou:
                best_iou = v
                best_j = j
        if best_iou >= iou_threshold and best_j >= 0:
            tp += 1
            matched_gt.add(best_j)

    precision = tp / len(pred)
    recall = tp / len(gt)
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    return precision, recall, f1


def evaluate(
    cfg: ScanExtractionSettings,
    image_paths: list[Path],
    gt: GroundTruth,
    iou_threshold: float = 0.5,
) -> dict[str, float]:
    """Средние F1 / IoU по набору изображений."""
    extractor = ScanExtractor(cfg)
    all_f1: list[float] = []
    all_iou: list[float] = []

    for img_path in image_paths:
        name = img_path.name
        gt_boxes = gt.get(name, [])

        try:
            image_bytes = img_path.read_bytes()
            assets = extractor.extract(image_bytes, page=0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("extract failed for %s: %s", name, exc)
            assets = []

        pred_boxes: list[BBox] = []
        for asset in assets:
            b = asset.box
            pred_boxes.append((b.x, b.y, b.width, b.height))

        _, _, f1 = _precision_recall_f1(pred_boxes, gt_boxes, iou_threshold)
        all_f1.append(f1)

        matched_ious: list[float] = []
        for p in pred_boxes:
            best = max((_iou(p, g) for g in gt_boxes), default=0.0)
            matched_ious.append(best)
        mean_img_iou = (
            sum(matched_ious) / len(matched_ious) if matched_ious else 0.0
        )
        all_iou.append(mean_img_iou)

    return {
        "mean_f1": sum(all_f1) / len(all_f1) if all_f1 else 0.0,
        "mean_iou": sum(all_iou) / len(all_iou) if all_iou else 0.0,
    }


# ---------------------------------------------------------------------------
# Grid-search
# ---------------------------------------------------------------------------

PARAM_GRID: dict[str, list[Any]] = {
    "blur_kernel": [3, 5, 7],
    "canny_threshold1": [30.0, 50.0, 80.0],
    "canny_threshold2": [100.0, 150.0, 200.0],
    "morph_kernel": [7, 9, 11],
    "morph_iterations": [1, 2],
    "min_area_ratio": [0.01, 0.02, 0.04],
    "min_fill_ratio": [0.4, 0.5, 0.6],
    "min_rectangularity": [0.6, 0.7, 0.8],
    "adaptive_block_size": [31, 51, 71],
    "adaptive_c": [5.0, 10.0, 15.0],
    "dark_morph_close_iter": [1, 2, 3],
    "dark_open_kernel": [3, 5, 7],
    "small_region_area_ratio": [0.03, 0.05, 0.08],
    "white_offset": [20.0, 30.0, 40.0],
    "merge_gap_px": [50, 100, 150],
}


def _random_config() -> ScanExtractionSettings:
    """Сэмплировать одну случайную конфигурацию из PARAM_GRID."""
    params: dict[str, Any] = {
        k: random.choice(v) for k, v in PARAM_GRID.items()
    }
    for odd_key in (
        "blur_kernel",
        "morph_kernel",
        "adaptive_block_size",
        "dark_open_kernel",
    ):
        if params.get(odd_key, 1) % 2 == 0:
            params[odd_key] = params[odd_key] + 1
    try:
        return ScanExtractionSettings(**params)
    except Exception:  # noqa: BLE001
        return ScanExtractionSettings()


def run_grid_search(
    image_paths: list[Path],
    gt: GroundTruth,
    n_random: int,
    iou_threshold: float,
) -> list[dict[str, Any]]:
    """Случайный grid-search (без Optuna)."""
    results: list[dict[str, Any]] = []
    for i in range(n_random):
        cfg = _random_config()
        t0 = time.perf_counter()
        metrics = evaluate(cfg, image_paths, gt, iou_threshold)
        elapsed = time.perf_counter() - t0
        entry: dict[str, Any] = {
            "trial": i,
            "params": cfg.model_dump(),
            **metrics,
            "elapsed_s": round(elapsed, 3),
        }
        results.append(entry)
        logger.info(
            "grid trial %d/%d  F1=%.3f  IoU=%.3f  (%.1fs)",
            i + 1,
            n_random,
            metrics["mean_f1"],
            metrics["mean_iou"],
            elapsed,
        )
    return results


# ---------------------------------------------------------------------------
# Optuna-search
# ---------------------------------------------------------------------------


def run_optuna_search(  # noqa: PLR0915
    image_paths: list[Path],
    gt: GroundTruth,
    n_trials: int,
    iou_threshold: float,
    study_name: str = "scan_extraction",
    storage: str | None = None,
) -> list[dict[str, Any]]:
    """Байесовская оптимизация через Optuna (TPE sampler)."""
    try:
        import optuna  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "optuna не установлен. Выполните: uv add optuna"
        ) from exc

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial: Any) -> float:
        params: dict[str, Any] = {
            "blur_kernel": trial.suggest_categorical(
                "blur_kernel", [3, 5, 7, 9]
            ),
            "canny_threshold1": trial.suggest_float(
                "canny_threshold1", 20.0, 100.0, step=5.0
            ),
            "canny_threshold2": trial.suggest_float(
                "canny_threshold2", 80.0, 250.0, step=10.0
            ),
            "morph_kernel": trial.suggest_categorical(
                "morph_kernel", [5, 7, 9, 11, 13]
            ),
            "morph_iterations": trial.suggest_int("morph_iterations", 1, 3),
            "min_area_ratio": trial.suggest_float(
                "min_area_ratio", 0.005, 0.06, step=0.005
            ),
            "max_area_ratio": trial.suggest_float(
                "max_area_ratio", 0.7, 0.95, step=0.05
            ),
            "min_aspect": trial.suggest_float(
                "min_aspect", 0.1, 0.4, step=0.05
            ),
            "max_aspect": trial.suggest_float(
                "max_aspect", 3.0, 7.0, step=0.5
            ),
            "min_fill_ratio": trial.suggest_float(
                "min_fill_ratio", 0.3, 0.7, step=0.05
            ),
            "min_rectangularity": trial.suggest_float(
                "min_rectangularity", 0.5, 0.9, step=0.05
            ),
            "adaptive_block_size": trial.suggest_categorical(
                "adaptive_block_size", [21, 31, 41, 51, 61, 71, 101]
            ),
            "adaptive_c": trial.suggest_float(
                "adaptive_c", 2.0, 20.0, step=1.0
            ),
            "dark_morph_close_iter": trial.suggest_int(
                "dark_morph_close_iter", 1, 4
            ),
            "dark_open_kernel": trial.suggest_categorical(
                "dark_open_kernel", [3, 5, 7, 9]
            ),
            "small_region_area_ratio": trial.suggest_float(
                "small_region_area_ratio", 0.01, 0.10, step=0.01
            ),
            "white_offset": trial.suggest_float(
                "white_offset", 10.0, 50.0, step=5.0
            ),
            "white_percentile": trial.suggest_float(
                "white_percentile", 90.0, 99.0, step=1.0
            ),
            "merge_gap_px": trial.suggest_int(
                "merge_gap_px", 30, 200, step=10
            ),
            "min_contour_area": trial.suggest_int(
                "min_contour_area", 2000, 15000, step=1000
            ),
            "crop_padding": trial.suggest_int("crop_padding", 0, 12, step=2),
            "margin_px": trial.suggest_int("margin_px", 20, 100, step=10),
        }
        try:
            cfg = ScanExtractionSettings(**params)
        except Exception:  # noqa: BLE001
            return 0.0

        metrics = evaluate(cfg, image_paths, gt, iou_threshold)
        trial.set_user_attr("mean_iou", metrics["mean_iou"])
        return metrics["mean_f1"]

    storage_kwargs: dict[str, Any] = {}
    if storage:
        storage_kwargs["storage"] = storage

    study = optuna.create_study(
        direction="maximize",
        study_name=study_name,
        sampler=optuna.samplers.TPESampler(seed=42),
        **storage_kwargs,
    )
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    results: list[dict[str, Any]] = []
    for t in study.trials:
        if t.value is None:
            continue
        results.append(
            {
                "trial": t.number,
                "params": t.params,
                "mean_f1": t.value,
                "mean_iou": t.user_attrs.get("mean_iou", 0.0),
            }
        )
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@app.command()
def tune(
    images: Annotated[
        Path,
        typer.Option(help="Папка с PNG/JPEG сканами страниц."),
    ],
    gt: Annotated[
        Path,
        typer.Option(help="JSON-файл с ground-truth bbox-ами."),
    ],
    mode: Annotated[
        str,
        typer.Option(help="Режим: grid | optuna"),
    ] = "optuna",
    trials: Annotated[
        int,
        typer.Option(help="Число попыток (grid: случайных, optuna: TPE)."),
    ] = 40,
    iou_threshold: Annotated[
        float,
        typer.Option(help="IoU-порог для TP при расчёте F1."),
    ] = 0.5,
    output: Annotated[
        Path,
        typer.Option(help="Куда сохранить результаты."),
    ] = Path("tuning_results.json"),
    top_n: Annotated[
        int,
        typer.Option(help="Вывести топ-N конфигураций в stdout."),
    ] = 5,
    optuna_storage: Annotated[
        str | None,
        typer.Option(
            help="Optuna storage URL (sqlite:///study.db). По умолчанию — in-memory."
        ),
    ] = None,
) -> None:
    """Подбор оптимальных констант ScanExtractionSettings."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if not images.is_dir():
        typer.echo(
            f"ERROR: --images должен быть папкой: {images}", err=True
        )
        raise typer.Exit(2)
    if not gt.exists():
        typer.echo(
            f"ERROR: файл --gt не найден: {gt}", err=True
        )
        raise typer.Exit(2)

    image_paths = sorted(
        p
        for p in images.iterdir()
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".tiff", ".bmp"}
    )
    if not image_paths:
        typer.echo(
            f"ERROR: не найдено изображений в {images}", err=True
        )
        raise typer.Exit(2)

    gt_data: GroundTruth = {}
    for item in json.loads(gt.read_text()):
        gt_data[item["image"]] = [
            (int(b[0]), int(b[1]), int(b[2]), int(b[3]))
            for b in item["boxes"]
        ]

    typer.echo(
        f"Режим: {mode}  |"
        f"  изображений: {len(image_paths)}  |"
        f"  попыток: {trials}"
    )

    t_start = time.perf_counter()

    if mode == "grid":
        results = run_grid_search(
            image_paths, gt_data, trials, iou_threshold
        )
    elif mode == "optuna":
        results = run_optuna_search(
            image_paths,
            gt_data,
            trials,
            iou_threshold,
            storage=optuna_storage,
        )
    else:
        typer.echo(f"ERROR: неизвестный режим: {mode}", err=True)
        raise typer.Exit(2)

    total_s = time.perf_counter() - t_start

    results.sort(
        key=lambda r: (r["mean_f1"], r["mean_iou"]), reverse=True
    )

    output.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    typer.echo(f"\nГотово за {total_s:.1f}с. Результаты → {output}")

    typer.echo(f"\nТоп-{min(top_n, len(results))} конфигураций:")
    typer.echo(f"{'#':<4}  {'F1':>6}  {'IoU':>6}  Параметры (ключевые)")
    for rank, r in enumerate(results[:top_n], 1):
        p = r["params"]
        snippet = (
            f"blur={p.get('blur_kernel')} "
            f"morph={p.get('morph_kernel')}×{p.get('morph_iterations')} "
            f"canny={p.get('canny_threshold1')}/{p.get('canny_threshold2')} "
            f"area={p.get('min_area_ratio'):.3f} "
            f"rect={p.get('min_rectangularity')} "
            f"adap={p.get('adaptive_block_size')} C={p.get('adaptive_c')}"
        )
        typer.echo(
            f"{rank:<4}  {r['mean_f1']:>6.3f}  {r['mean_iou']:>6.3f}  {snippet}"
        )

    if results:
        best = results[0]["params"]
        env_lines = "\n".join(
            f"BIBLIOATOM_SCAN_EXTRACTION__{k.upper()}={v}"
            for k, v in best.items()
        )
        typer.echo(
            f"\n# Лучшая конфигурация — вставьте в .env:\n{env_lines}"
        )


if __name__ == "__main__":
    app()
