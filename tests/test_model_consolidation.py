"""통합 모델의 버전 선택과 실행 간 상태 격리를 검증한다."""

import importlib
import io
import runpy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class ModelConsolidationTests(unittest.TestCase):
    names = ("model_jp", "model_kr", "model_week_jp", "model_week_kr")

    def test_all_versions_keep_checkpoint_paths_and_explicit_arguments(self):
        for name in self.names:
            model = importlib.import_module(f"create_model.{name}")
            for version in ("v1", "v2", "v3"):
                with self.subTest(name=name, version=version):
                    args = model.parse_args(["--model-version", version])
                    suffix = "" if version == "v1" else f"_{version}"
                    self.assertEqual(Path(args.model_out).name, f"{name}{suffix}.pt")
                    custom = model.parse_args([
                        "--model-version", version, "--table", "custom_table",
                        "--end-date", "2020-01-01", "--model-out", "custom.pt",
                    ])
                    self.assertEqual((custom.table, custom.end_date, custom.model_out),
                                     ("custom_table", "2020-01-01", "custom.pt"))
            self.assertEqual(model.parse_args([]).model_version, "v3")

    def test_default_training_checkpoint_matches_default_prediction(self):
        from predict.common import parse_args
        from create_model.model_jp import load_model_checkpoint
        for name in self.names:
            with self.subTest(name=name):
                model = importlib.import_module(f"create_model.{name}")
                prediction = importlib.import_module("predict." + name.replace("model_", "predict_", 1))
                training = model.parse_args([])
                spec = prediction.build_prediction_spec()
                inference = parse_args(spec, [])
                self.assertEqual(training.model_version, "v3")
                self.assertEqual(Path(training.model_out).name, f"{name}_v3.pt")
                self.assertEqual(training.model_version, spec.model_version)
                self.assertEqual(Path(training.model_out).resolve(), Path(inference.model).resolve())
                for field in ("table", "seq_len", "horizon_days", "rise_threshold", "d_model", "nhead", "num_encoder_layers", "dim_feedforward", "dropout"):
                    self.assertEqual(getattr(training, field), getattr(inference, field), field)
                columns, compute, mode, _ = model.get_probability_config(training.model_version)
                feature_model = importlib.import_module("create_model." + spec.v2_module)
                predicted_columns, predicted_compute, predicted_mode, _ = feature_model.get_probability_config(spec.model_version)
                self.assertEqual(columns, predicted_columns)
                self.assertIs(compute, predicted_compute)
                self.assertEqual(mode, spec.model_mode or predicted_mode)
                architecture = {field: getattr(training, field) for field in ("d_model", "nhead", "num_encoder_layers", "dim_feedforward", "dropout")}
                trained = model.StockTransformer(input_size=len(columns), **architecture)
                predicted = model.StockTransformer(input_size=len(predicted_columns), **architecture)
                # 실제 모델 파일을 덮어쓰지 않고 저장·로드 호환성을 검증한다.
                checkpoint = io.BytesIO()
                torch.save({"state_dict": trained.state_dict(), "ha_feature_mode": mode}, checkpoint)
                checkpoint.seek(0)
                result = predicted.load_state_dict(load_model_checkpoint(checkpoint, predicted_mode))
                self.assertFalse(result.missing_keys)
                self.assertFalse(result.unexpected_keys)

    def test_v3_defaults_use_split_sources_and_training_cutoff(self):
        from create_model.split_source import training_cutoff_date
        for name in self.names:
            model = importlib.import_module(f"create_model.{name}")
            args = model.parse_args(["--model-version", "v3"])
            market = name.rsplit("_", 1)[1]
            weekly = "week_" if "week" in name else ""
            self.assertEqual(args.table, f"stock_data_split_{weekly}{market}")
            self.assertEqual(args.end_date, training_cutoff_date())

    def test_feature_versions_and_dataset_filters_are_isolated(self):
        for name in self.names:
            with self.subTest(name=name):
                model = importlib.import_module(f"create_model.{name}")
                raw = np.full((150, len(model._RAW_COLS)), 100, dtype=np.float32)
                cols2, compute2, mode2, eligible2 = model.get_probability_config("v2")
                cols3, compute3, mode3, eligible3 = model.get_probability_config("v3")
                before = compute2(raw).copy()
                features3 = compute3(raw)
                self.assertEqual(len(cols2), 5)
                self.assertEqual(len(cols3), 8 if "week" in name else 10)
                np.testing.assert_array_equal(features3[:, :5], before)
                np.testing.assert_array_equal(compute2(raw), before)
                self.assertNotEqual(mode2, mode3)
                self.assertIsNone(eligible2)
                self.assertTrue(callable(eligible3))
                kwargs = dict(table="unused", codes=[], start_date=None, end_date=None,
                              seq_len=120, horizon_days=20, rise_threshold=.05,
                              cutoff_date="2020-01-01", split="train", log_codes=False,
                              log_every=1)
                ds3 = model.WindowIterableDatasetV2(**kwargs, model_version="v3")
                ds2 = model.WindowIterableDatasetV2(**kwargs, model_version="v2")
                self.assertIs(ds3.compute_features, compute3)
                self.assertIs(ds2.compute_features, compute2)
                self.assertIsNone(ds2.sample_eligibility)
                with self.assertRaises(ValueError):
                    model.get_probability_config("invalid")

    def test_main_dispatches_without_training_or_database_access(self):
        for name in self.names:
            model = importlib.import_module(f"create_model.{name}")
            for version in ("v1", "v2", "v3"):
                with patch.object(model, "_main_v1") as legacy, patch.object(model, "_main_probability") as probability:
                    model.main(["--model-version", version])
                    selected, unused = (legacy, probability) if version == "v1" else (probability, legacy)
                    selected.assert_called_once()
                    unused.assert_not_called()
                    self.assertEqual(selected.call_args.args[0].model_version, version)

    def test_probability_training_uses_matching_features_filters_and_checkpoint(self):
        for name in self.names:
            model = importlib.import_module(f"create_model.{name}")
            for version in ("v3", "v2", "v3"):
                with self.subTest(name=name, version=version), \
                     patch.object(model, "load_codes", return_value=["TEST"]), \
                     patch.object(model, "get_cutoff_date", return_value=pd.Timestamp("2020-01-01")), \
                     patch.object(model, "_FilteredTee", return_value=io.StringIO()), \
                     patch.object(sys, "stdout", new_callable=io.StringIO), \
                     patch.object(model, "StockTransformer") as transformer, \
                     patch.object(model, "_probability_load_checkpoint", return_value={}) as checkpoint, \
                     patch.object(model, "_probability_train_loop") as train:
                    args = model.parse_args(["--model-version", version, "--resume", "fixture.pt"])
                    model._main_probability(args)
                    columns, compute, mode, eligible = model.get_probability_config(version)
                    self.assertEqual(transformer.call_args.kwargs["input_size"], len(columns))
                    self.assertEqual(checkpoint.call_args.args, ("fixture.pt", mode))
                    train.assert_called_once()
                    self.assertEqual(train.call_args.args[-1], mode)
                    self.assertEqual(train.call_args.args[6], args.model_out)
                    for loader in train.call_args.args[1:3]:
                        self.assertIs(loader.dataset.compute_features, compute)
                        self.assertIs(loader.dataset.sample_eligibility, eligible)

    def test_all_prediction_entrypoints_use_consolidated_models(self):
        predict_dir = str(Path(__file__).resolve().parents[1] / "src/predict")
        with patch.object(sys, "path", [predict_dir, *sys.path]):
            for name in self.names:
                model = importlib.import_module(f"create_model.{name}")
                for version in ("v2", "v3"):
                    entrypoint = Path(predict_dir) / (name.replace("model_", "predict_", 1) + ".py")
                    with patch("predict.common.run_prediction") as run, patch.object(sys, "argv", [str(entrypoint), "--model-version", version]):
                        runpy.run_path(str(entrypoint), run_name="__main__")
                        run.assert_called_once()
                        spec = run.call_args.args[0]
                    self.assertEqual(spec.base_module, name)
                    self.assertEqual(spec.v2_module, name)
                    self.assertEqual(spec.model_version, version)
                    self.assertEqual(spec.model_file, f"{name}_{version}.pt")
                    if spec.model_mode:
                        self.assertEqual(spec.model_mode, model.get_probability_config(version)[2])

    def test_prediction_cli_preserves_defaults_and_explicit_overrides(self):
        from predict.common import parse_args
        for name in self.names:
            prediction = importlib.import_module("predict." + name.replace("model_", "predict_", 1))
            for version in ("v2", "v3"):
                spec = prediction.build_prediction_spec(version)
                args = parse_args(spec, [])
                self.assertEqual(args.table, spec.table)
                self.assertEqual(Path(args.model).name, spec.model_file)
                self.assertEqual(args.require_upper_band_breakout, version == "v3")
                self.assertEqual(args.max_extension_ratio, .50 if version == "v3" else None)
                custom = ["--table", "custom_table", "--model", "custom.pt", "--no-require-upper-band-breakout"]
                with patch("predict.common.run_prediction") as run:
                    prediction.main(["--model-version", version, *custom])
                    selected = run.call_args.args[0]
                    parsed = parse_args(selected, run.call_args.kwargs["argv"])
                    self.assertEqual(selected, spec)
                    self.assertEqual((parsed.table, parsed.model), ("custom_table", "custom.pt"))
                    self.assertFalse(parsed.require_upper_band_breakout)
                    self.assertEqual(run.call_args.kwargs["weekly"], "week" in name)
            with patch("predict.common.run_prediction") as run:
                prediction.main([])
                self.assertEqual(run.call_args.args[0].model_version, "v3")
            with self.assertRaises(ValueError):
                prediction.build_prediction_spec("v1")
            with patch("predict.common.run_prediction") as run, patch.object(sys, "stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as error:
                    prediction.main(["--model-version", "v1"])
                self.assertEqual(error.exception.code, 2)
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
