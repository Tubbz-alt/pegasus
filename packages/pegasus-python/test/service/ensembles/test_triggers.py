import pickle
import queue
import subprocess
import threading
from collections import defaultdict
from pathlib import Path
from subprocess import CompletedProcess
from tempfile import mkdtemp

import pytest

import Pegasus.service.ensembles.trigger as trigger
from Pegasus.service.ensembles.commands import StartPatternIntervalTriggerCommand
from Pegasus.service.ensembles.trigger import (
    TriggerManagerMessage,
    _PatternIntervalTrigger,
    _Trigger,
    _TriggerManagerMessageType,
)


# helper function to create a defaultdict(defaultdict(list))
# that can be serialized
def dd_list():
    return defaultdict(list)


# use to override Pegasus.service.ensembles.trigger._TRIGGER_DIR so that any files
# created by the trigger manager go to a temporary location as opposed to
# ~/.pegasus/triggers
@pytest.fixture(scope="function")
def trigger_dir():
    td = Path(mkdtemp())
    yield td
    for f in td.iterdir():
        f.unlink()
    td.rmdir()


class TestTriggerManagerMessage:
    def test_valid_msg(self):
        msg = TriggerManagerMessage(
            TriggerManagerMessage.STOP_TRIGGER, ensemble="casa", trigger_name="20s*.txt"
        )
        assert msg._type == _TriggerManagerMessageType.STOP_TRIGGER
        assert msg.kwargs == {"ensemble": "casa", "trigger_name": "20s*.txt"}


class TestTriggerDispatcher:
    def test_state_file_written(self, trigger_dir, monkeypatch):
        monkeypatch.setattr(trigger, "_TRIGGER_DIR", trigger_dir)
        trigger._TriggerDispatcher(None)
        with (trigger_dir / "running.p").open("rb") as f:
            assert pickle.load(f) == set()

    def test_test_removal_of_references_to_stopped_worker_threads(
        self, trigger_dir, monkeypatch
    ):
        monkeypatch.setattr(trigger, "_TRIGGER_DIR", trigger_dir)
        trigger_dispatcher = trigger._TriggerDispatcher(None)

        # testing that the "currently running threads" dict is cleaned up based
        # correctly based on what is in the "checkout" queue

        # fill up "currently running threads"
        trigger_dispatcher.running = {"t1": None, "t2": None, "t3": None}

        # enqueue "threads to be removed from running because they are now shutting down"
        trigger_dispatcher.checkout.put("t1")
        trigger_dispatcher.checkout.put("t2")

        # testing run right up until receiving a new message for work
        with pytest.raises(AttributeError):
            trigger_dispatcher.run()

        assert len(trigger_dispatcher.running) == 1
        assert "t3" in trigger_dispatcher.running

        with (trigger_dir / "running.p").open("rb") as f:
            state_file_contents = pickle.load(f)
            assert "t3" in state_file_contents
            assert len(state_file_contents) == 1

    def test_update_submited_workflows_file(self, trigger_dir, monkeypatch):
        monkeypatch.setattr(trigger, "_TRIGGER_DIR", trigger_dir)
        trigger_dispatcher = trigger._TriggerDispatcher(None)

        # testing that newly submitted workflows are added to the submitted
        # workflows file

        # enter items into submitted queue to be processed
        trigger_dispatcher.submitted.put(("ens1", "trigger", "wf1"))

        # update submitted workflows file
        trigger_dispatcher.update_submitted_workflows_file()

        # test that file updated correctly
        with (trigger_dir / "submitted.p").open("rb") as f:
            workflows = pickle.load(f)

        assert dict(workflows) == {"ens1": {"trigger": ["wf1"]}}

        # enter items into submitted queue to be processed
        trigger_dispatcher.submitted.put(("ens1", "trigger", "wf2"))
        trigger_dispatcher.submitted.put(("ens2", "trigger", "wf1"))

        # update submitted workflows file
        trigger_dispatcher.update_submitted_workflows_file()

        # test that file updated correctly and existing data not overwritten
        with (trigger_dir / "submitted.p").open("rb") as f:
            workflows = pickle.load(f)

        assert dict(workflows) == {
            "ens1": {"trigger": ["wf1", "wf2"]},
            "ens2": {"trigger": ["wf1"]},
        }

    @pytest.mark.parametrize(
        "msg, func",
        [
            (
                TriggerManagerMessage(
                    TriggerManagerMessage.STOP_TRIGGER, test_kwarg=None
                ),
                "stop_trigger_handler",
            ),
            (
                TriggerManagerMessage(
                    TriggerManagerMessage.START_PATTERN_INTERVAL_TRIGGER,
                    test_kwarg=None,
                ),
                "start_pattern_interval_trigger_handler",
            ),
        ],
    )
    def test_do_work_based_on_msg_received(self, trigger_dir, monkeypatch, msg, func):
        # testing that the correct handler is called; each handler is being passed
        # an invalid argument so that the 'while True' loop in the run() method can
        # be exited by means of an exception
        monkeypatch.setattr(trigger, "_TRIGGER_DIR", trigger_dir)

        mailbox = queue.Queue()
        mailbox.put(msg)
        trigger_dispatcher = trigger._TriggerDispatcher(mailbox)

        with pytest.raises(TypeError) as e:
            trigger_dispatcher.run()

        assert func in str(e)

    def test_start_trigger_handler(self, trigger_dir, monkeypatch, mocker):
        monkeypatch.setattr(trigger, "_TRIGGER_DIR", trigger_dir)
        mocker.patch("threading.Thread.start")

        trigger_dispatcher = trigger._TriggerDispatcher(None)
        trigger_dispatcher.start_pattern_interval_trigger_handler(
            ensemble="test-ensemble",
            trigger_name="test-trigger",
            workflow_name_prefix="abc",
            file_patterns=["/random_file_that_shouldnt_exist"],
            workflow_script="/workflow.py",
            interval=1,
        )

        assert "test-ensemble::test-trigger" in trigger_dispatcher.running

        with (trigger_dir / "running.p").open("rb") as f:
            assert "test-ensemble::test-trigger" in pickle.load(f)

        threading.Thread.start.assert_called_once_with()

    def test_stop_trigger_handler(self, trigger_dir, monkeypatch, mocker):
        monkeypatch.setattr(trigger, "_TRIGGER_DIR", trigger_dir)

        trigger_dispatcher = trigger._TriggerDispatcher(None)

        # start trigger thread (that will do nothing)
        trigger_dispatcher.start_pattern_interval_trigger_handler(
            ensemble="test-ensemble",
            trigger_name="test-trigger",
            workflow_name_prefix="abc",
            file_patterns=["/random_file_that_shouldnt_exist"],
            workflow_script="/workflow.py",
            interval=1,
        )

        assert "test-ensemble::test-trigger" in trigger_dispatcher.running

        with (trigger_dir / "running.p").open("rb") as f:
            assert "test-ensemble::test-trigger" in pickle.load(f)

        trigger_thread = trigger_dispatcher.running["test-ensemble::test-trigger"]

        # issue shutdown command
        trigger_dispatcher.stop_trigger_handler(
            ensemble="test-ensemble", trigger_name="test-trigger"
        )

        # wait for thread to terminate
        trigger_thread.join()

        # check that bookkeeping was done correctly
        with (trigger_dir / "running.p").open("rb") as f:
            assert set() == pickle.load(f)

        assert "test-ensemble::test-trigger" not in trigger_dispatcher.running


class TestTrigger:
    def test_shutdown(self):
        t = _Trigger(
            ensemble="ens",
            trigger_name="t",
            checkout=queue.Queue(),
            submitted=queue.Queue(),
        )
        assert t.stop_event.isSet() == False
        t.shutdown()

        # ensure that stop even is set
        assert t.stop_event.isSet() == True

        # ensure that t has advertised itself to the 'checkout' queue
        assert t.checkout.get() == t.name

    def test_update_submitted_workflows(self):
        # create trigger (without starting)
        t = _Trigger(
            ensemble="ensemble_name",
            trigger_name="trigger_name",
            checkout=queue.Queue(),
            submitted=queue.Queue(),
        )

        # update file
        t.update_submitted_workflows("test_workflow")

        # test workflow has been added to submitted queue
        assert t.submitted.get() == ("ensemble_name", "trigger_name", "test_workflow")


class TestPatternIntervalTrigger:
    def test_submit_command(self, mocker):
        mocker.patch("subprocess.run")

        # create test input files
        temp_dir = Path(mkdtemp())
        f1 = temp_dir / "f1.jpg"
        f2 = temp_dir / "f2.csv"

        with f1.open("w") as f, f2.open("w") as ff:
            f.write("test file1")
            ff.write("test file2")

        t = _PatternIntervalTrigger(
            submitted=queue.Queue(),
            checkout=queue.Queue(),
            ensemble="test-ensemble",
            trigger_name="test-trigger",
            workflow_name_prefix="wf",
            file_patterns=[
                str(temp_dir.resolve() / "*.jpg"),
                str(temp_dir.resolve() / "*.csv"),
            ],
            workflow_script="/workflow.py",
            interval=2,
            timeout=1,
            additional_args="arg1 --flag arg2",
        )

        t.run()

        # ensure pegasus-em submit command properly built up
        args = subprocess.run.call_args[0][0]
        assert args[0:2] == ["pegasus-em", "submit"]
        assert "test-ensemble.wf_" in args[2]
        assert args[3:] == [
            "/workflow.py",
            "arg1",
            "--flag",
            "arg2",
            "--inputs",
            str(f1.resolve()),
            str(f2.resolve()),
        ]

        # cleanup
        f1.unlink()
        f2.unlink()
        temp_dir.rmdir()

    def test_symlinks_resolved(self, mocker):
        """
        Ensure that symlinks picked up by glob pattern are resolved and the
        appropriate 'pegasus-em submit' command is generated.
        """
        mocker.patch("subprocess.run")

        # create test input file and symlink
        temp_dir = Path(mkdtemp())
        f1 = temp_dir / "f1.jpg"
        f1_link = temp_dir / "f1.link"

        with f1.open("w") as f:
            f.write("test file1")

        f1_link.symlink_to(target=f1)

        # create trigger using *.link as the file pattern to look for
        t = _PatternIntervalTrigger(
            submitted=queue.Queue(),
            checkout=queue.Queue(),
            ensemble="test-ensemble",
            trigger_name="test-trigger",
            workflow_name_prefix="wf",
            file_patterns=[str(temp_dir / "*.link")],
            workflow_script="/workflow.py",
            interval=2,
            timeout=1,
            additional_args="arg1 --flag arg2",
        )

        t.run()

        # ensure pegasus-em submit command properly built up
        args = subprocess.run.call_args[0][0]
        assert args[0:2] == ["pegasus-em", "submit"]
        assert "test-ensemble.wf_" in args[2]
        assert args[3:] == [
            "/workflow.py",
            "arg1",
            "--flag",
            "arg2",
            "--inputs",
            str(f1.resolve()),
        ]

        # cleanup
        f1.unlink()
        f1_link.unlink()
        temp_dir.rmdir()

    def test_multiple_symlinks(self, mocker):
        """
        Ensure that symlinks are stat'ed and not the files they link to. The scenario
        is as follows:

        create f1.txt (ts: 1)
        create f2.txt (ts: 2)

        start trigger with pattern *.link on a 15s interval

        create f1.link (ts: 10) -> f1.txt

        trigger picks up f1.txt

        create f2.link (ts: 20) -> f2.txt

        trigger picks up f2.txt
        """
        mocker.patch(
            "subprocess.run",
            return_value=CompletedProcess(None, returncode=0, stdout="", stderr=""),
        )

        # create test input file and symlink
        temp_dir = Path(mkdtemp())
        f1 = temp_dir / "f1"
        f2 = temp_dir / "f2"

        f1_link = temp_dir / "f1.link"
        f2_link = temp_dir / "f2.link"

        with f1.open("w") as f:
            f.write("test file1")

        with f2.open("w") as f:
            f.write("test file2")

        f1_link.symlink_to(target=f1)

        # create trigger using *.link as the file pattern to look for
        t = _PatternIntervalTrigger(
            submitted=queue.Queue(),
            checkout=queue.Queue(),
            ensemble="test-ensemble",
            trigger_name="test-trigger",
            workflow_name_prefix="wf",
            file_patterns=[str(temp_dir / "*.link")],
            workflow_script="/workflow.py",
            interval=2,
            timeout=1,
            additional_args="arg1 --flag arg2",
        )

        t.run()

        # ensure pegasus-em submit command properly built up
        # f1 should be passed as an input file
        args = subprocess.run.call_args[0][0]
        assert args[0:2] == ["pegasus-em", "submit"]
        assert "test-ensemble.wf_" in args[2]
        assert args[3:] == [
            "/workflow.py",
            "arg1",
            "--flag",
            "arg2",
            "--inputs",
            str(f1.resolve()),
        ]

        f2_link.symlink_to(target=f2)
        t.run()

        # ensure pegasus-em submit command properly built up
        # f2 should be passed as an input file
        args = subprocess.run.call_args[0][0]
        assert args[0:2] == ["pegasus-em", "submit"]
        assert "test-ensemble.wf_" in args[2]
        assert args[3:] == [
            "/workflow.py",
            "arg1",
            "--flag",
            "arg2",
            "--inputs",
            str(f2.resolve()),
        ]

        # cleanup
        f1.unlink()
        f2.unlink()
        f1_link.unlink()
        f2_link.unlink()
        temp_dir.rmdir()


class TestStartPatternIntervalTriggerCommand:
    @pytest.mark.parametrize(
        "input, expected",
        [
            ("1s", 1),
            (" 1s", 1),
            (" 1  s ", 1),
            ("2m", 120),
            ("3H", 60 * 60 * 3),
            ("1d", 60 * 60 * 24),
        ],
    )
    def test_to_seconds(self, input, expected):
        assert StartPatternIntervalTriggerCommand.to_seconds(input) == expected

    @pytest.mark.parametrize("input", [("abc"), ("x 1s"), (""), ("1sd"), ("1s d")])
    def test_to_seconds_invalid_interval(self, input):
        with pytest.raises(ValueError) as e:
            StartPatternIntervalTriggerCommand.to_seconds(input)

        assert "invalid interval: {}".format(input) in str(e)

    def test_to_seconds_nonzero_interval(self):
        with pytest.raises(ValueError) as e:
            StartPatternIntervalTriggerCommand.to_seconds("0d")

        assert "invalid interval: 0" in str(e)
