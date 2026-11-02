"""Append live regression diagnostics without waiting for the pytest session end."""
import json
import os
from pathlib import Path

def pytest_runtest_logreport(report):
    target=os.environ.get('PIPELINE_TEST_EVENT_LOG')
    if target and (report.when=='call' or report.failed or report.skipped):
        row={'id':report.nodeid,'phase':report.when,'status':report.outcome,'duration_s':report.duration}
        if report.failed or report.skipped:
            row['diagnostic']=str(report.longrepr)
        with Path(target).open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(row,ensure_ascii=False)+'\n')

def pytest_collectreport(report):
    target=os.environ.get('PIPELINE_TEST_EVENT_LOG')
    if target and report.failed:
        with Path(target).open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'id':report.nodeid,'phase':'collection','status':'failed','diagnostic':str(report.longrepr)},ensure_ascii=False)+'\n')
