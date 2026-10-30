"""세 가지 공개 실행 패턴과 내부 단계 순서·실패 전파·기존 예약 전환 계약."""
import contextlib
import tempfile
import unittest
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from batch.cli import build_parser
from batch.config import BatchConfig, MODES, command_for, tasks_for
from batch.runner import run_batch
from batch.service import BatchManager


class PatternContractTests(unittest.TestCase):
    def test_only_three_public_patterns(self):
        self.assertEqual(MODES, ('collect', 'predict', 'full'))
        for mode in ('base','indicators','split','daily','weekly'):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                tasks_for(mode)
            with contextlib.redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                build_parser().parse_args(['run','--market','jp','--mode',mode])

    def test_collection_always_calculates_indicators_before_prediction(self):
        self.assertEqual(tasks_for('collect'), ('base','indicators'))
        self.assertEqual(tasks_for('predict'), ('daily','weekly'))
        self.assertEqual(tasks_for('full'), ('base','indicators','daily','weekly'))
        for market in ('JP','KR'):
            for mode in MODES:
                for task in tasks_for(mode):
                    command = command_for(market, task)
                    self.assertNotEqual(command[1], f'dataset/dataset_{market.lower()}.py')
                    self.assertTrue((Path(__file__).resolve().parents[1]/'src'/command[1]).is_file())


class PipelineExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = BatchConfig(root, root/'state', root/'logs', 10)

    def tearDown(self):
        self.temp.cleanup()

    def run_pipeline(self, mode, codes):
        processes=[]
        for code in codes:
            process=Mock()
            process.stdout=BytesIO(b'')
            process.wait.return_value=code
            processes.append(process)
        with patch('batch.runner.subprocess.Popen',side_effect=processes) as popen, contextlib.redirect_stdout(StringIO()):
            error=None
            try:
                run_batch('JP',mode,config=self.config,stream_subprocess_output=False)
            except RuntimeError as failure:
                error=failure
        scripts=[call.args[0][call.args[0].index('--')+2] for call in popen.call_args_list]
        return scripts,error

    def test_full_runs_real_runner_stages_in_order_without_real_processes(self):
        scripts,error=self.run_pipeline('full',[0,0,0,0])
        self.assertIsNone(error)
        self.assertEqual(scripts, ['dataset/base_dataset_jp.py','dataset/indicator_dataset_jp.py','predict/predict_jp.py','predict/predict_week_jp.py'])

    def test_collection_failure_prevents_indicator_and_predictions(self):
        scripts,error=self.run_pipeline('full',[1])
        self.assertIsNotNone(error)
        self.assertEqual(scripts,['dataset/base_dataset_jp.py'])

    def test_indicator_failure_prevents_both_predictions(self):
        scripts,error=self.run_pipeline('full',[0,1])
        self.assertIsNotNone(error)
        self.assertEqual(scripts,['dataset/base_dataset_jp.py','dataset/indicator_dataset_jp.py'])

    def test_collection_contains_both_stages_without_prediction(self):
        scripts,error=self.run_pipeline('collect',[0,0])
        self.assertIsNone(error)
        self.assertEqual(scripts,['dataset/base_dataset_jp.py','dataset/indicator_dataset_jp.py'])

    def test_recovery_migrates_old_schedule_and_queue_without_rewriting_completed_history(self):
        manager=BatchManager(self.config)
        with manager.db() as conn:
            conn.execute("UPDATE schedules SET mode='split', cron='0 12 * * 1-5' WHERE market='JP'")
            conn.execute("UPDATE schedules SET mode='weekly' WHERE market='KR'")
            for index,mode,status in [(1,'base','queued'),(2,'indicators','queued'),(3,'daily','queued'),(4,'weekly','queued'),(5,'split','succeeded')]:
                conn.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,NULL,NULL,NULL)',(f'{index:032x}','JP',mode,'web',status,f'2026-10-30T0{index}:00:00+00:00'))
        manager.recover()
        manager.recover()  # 동일 상태 재실행은 멱등적입니다.
        self.assertEqual([row['mode'] for row in manager.schedules()],['collect','predict'])
        self.assertEqual(manager.detail(f'{1:032x}')['mode'],'collect')
        self.assertEqual(manager.detail(f'{2:032x}')['status'],'cancelled')
        self.assertEqual(manager.detail(f'{3:032x}')['mode'],'predict')
        self.assertEqual(manager.detail(f'{4:032x}')['status'],'cancelled')
        self.assertEqual(manager.detail(f'{5:032x}')['mode'],'split')
        self.assertEqual(manager.detail(f'{5:032x}')['status'],'succeeded')

    def test_deprecated_inputs_are_rejected_by_api_manager(self):
        manager=BatchManager(self.config)
        for mode in ('base','indicators','split','daily','weekly'):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                manager.submit('JP',mode)
            with self.assertRaises(ValueError):
                manager.update_schedule('JP',{'enabled':True,'cron':'0 12 * * *','mode':mode})


if __name__ == '__main__':
    unittest.main()
