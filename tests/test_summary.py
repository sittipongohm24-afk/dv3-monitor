import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import summary


def sample():
    return {'title':'อัปเดตเกม', 'subtitle':'ข้อมูลทดสอบ', 'cards':[{
        'source':'13:1','title':'มังกรใหม่','highlight':'รางวัล 100 เพชร',
        'period':'ดูวันสิ้นสุดในประกาศ','points':['เข้าเกมเพื่อรับรางวัล']} ]}


class SummaryTests(unittest.TestCase):
    def test_missing_source_rejected(self):
        with self.assertRaises(ValueError):
            summary.validate_summary(sample(), {'13:1':{}, '5:2':{}})

    def test_oversize_rejected(self):
        data = sample()
        data['cards'][0]['points'] = ['ก'*101]
        with self.assertRaises(ValueError):
            summary.validate_summary(data, {'13:1':{}})

    def test_enqueue_idempotent_and_sent_not_requeued(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(summary, 'OUTBOX', Path(directory)/'state.json'):
            post = {'board':'13','id':'1','title':'test'}
            summary.enqueue(post, {'content':'hello'})
            summary.enqueue(post, {'content':'hello'})
            state = summary.read_state()
            self.assertEqual(len(state['pending']), 1)
            state['pending'].clear()
            state['sent'].append('13:1')
            summary.save_state(state)
            summary.enqueue(post, {})
            self.assertFalse(summary.read_state()['pending'])

    def test_delivery_failure_retains_pending(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(summary, 'OUTBOX', Path(directory)/'state.json'), patch.object(summary, 'OUTPUT', Path(directory)/'output'), patch.dict(os.environ, {'GEMINI_API_KEY':'fake', 'DISCORD_SUMMARY_WEBHOOK_URL':'fake'}):
            summary.enqueue({'board':'13','id':'1','title':'test'}, {'content':'hello'})
            with patch.object(summary, 'prepare', return_value=([], {})), patch.object(summary, 'summarize', return_value=sample()), patch.object(summary, 'render'), patch.object(summary, 'send_image', side_effect=RuntimeError):
                with self.assertRaises(RuntimeError): summary.run()
            self.assertIn('13:1', summary.read_state()['pending'])
            with patch.object(summary, 'prepare', return_value=([], {})), patch.object(summary, 'summarize') as ai, patch.object(summary, 'render'), patch.object(summary, 'send_image'):
                summary.run()
                ai.assert_not_called()
            self.assertEqual(summary.read_state()['sent'], ['13:1'])
            self.assertFalse(summary.read_state()['pending'])

    def test_missing_credentials_does_not_call_ai(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(summary, 'summarize') as ai:
            summary.run()
            ai.assert_not_called()

if __name__ == '__main__': unittest.main()
