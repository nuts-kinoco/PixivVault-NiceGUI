import pytest

from pixivvault_nicegui.fixtures import generate_demo_artworks
from pixivvault_nicegui.learning import LearningState, MemoryJudgmentLedger
from pixivvault_nicegui.models import RequestToken


def loaded(state):
    token = state.begin_load()
    assert state.finish_load(token)
    return token


def judge_current(state, choice="like", operation_id="op-1"):
    return state.judge(choice, expected_work_id=state.current_artwork.work_id,
                       expected_generation=state.generation, operation_id=operation_id)


def test_load_order_failure_retry_and_forged_tokens():
    state = LearningState(generate_demo_artworks(2))
    first = state.begin_load()
    second = state.begin_load()
    assert first != second
    assert not state.finish_load(first)
    assert not state.finish_load(RequestToken(second.generation, second.work_id))
    assert not state.finish_load(second, success=False)
    assert state.last_error == "image load failed"
    assert not judge_current(state)
    assert state.index == 0
    third = loaded(state)
    assert not state.finish_load(second)
    assert not state.finish_load(third)
    assert state.ready and state.last_error is None


@pytest.mark.parametrize("choice", ["skip", "hate", "like", "superLike"])
def test_choices_advance_only_after_load_and_save(choice):
    ledger = MemoryJudgmentLedger()
    state = LearningState(generate_demo_artworks(2), ledger)
    assert not judge_current(state, choice)
    token = loaded(state)
    assert not state.judge(choice, expected_work_id="wrong",
                           expected_generation=token.generation, operation_id="wrong")
    assert not state.judge(choice, expected_work_id=token.work_id,
                           expected_generation=token.generation - 1, operation_id="wrong")
    assert judge_current(state, choice)
    assert (state.index, state.ready, state.can_undo) == (1, False, True)
    assert ledger.entries[0].choice == choice
    assert not state.judge(choice, expected_work_id=token.work_id,
                           expected_generation=token.generation, operation_id="op-1")
    assert not judge_current(state, choice, "op-2")
    loaded(state)
    assert not judge_current(state, choice, "op-1")
    assert state.index == 1 and state.last_error is not None
    assert judge_current(state, choice, "op-2")
    assert state.current_artwork is None and not state.ready


def test_single_undo_reloads_same_artwork_and_rejects_replays():
    ledger = MemoryJudgmentLedger()
    state = LearningState(generate_demo_artworks(3), ledger)
    loaded(state)
    assert judge_current(state, operation_id="a")
    loaded(state)
    assert judge_current(state, operation_id="b")
    old_generation = state.generation
    assert state.undo(operation_id="undo-b")
    assert state.index == 1 and state.current_artwork.work_id == "demo-0002"
    assert state.generation > old_generation and not state.ready
    assert not state.undo(operation_id="undo-b")
    assert state.index == 1 and not state.can_undo
    loaded(state)
    assert judge_current(state, operation_id="c")
    assert not state.undo(operation_id="undo-b")
    assert state.index == 2 and state.can_undo and "duplicate" in state.last_error
    assert state.undo(operation_id="undo-c")
    assert not state.undo(operation_id="undo-a")
    assert state.index == 1 and not state.can_undo
    assert not state.undo(operation_id="unused")
    assert [entry.operation_id for entry in ledger.entries] == ["a"]


def test_memory_ledger_keeps_lifo_and_operation_id_guards():
    from pixivvault_nicegui.learning import Judgment

    ledger = MemoryJudgmentLedger()
    first, second = Judgment("a", "like", "1"), Judgment("b", "hate", "2")
    ledger.record(first)
    ledger.record(second)
    with pytest.raises(ValueError, match="latest"):
        ledger.revert(first, "undo-first")
    ledger.revert(second, "undo-second")
    with pytest.raises(ValueError, match="duplicate"):
        ledger.revert(first, "undo-second")
    ledger.revert(first, "undo-first")
    assert not ledger.entries


def test_store_and_undo_exceptions_leave_state_unchanged_and_retryable():
    class FlakyLedger(MemoryJudgmentLedger):
        fail_record = True
        fail_revert = True

        def record(self, judgment):
            if self.fail_record:
                raise OSError("disk offline")
            super().record(judgment)

        def revert(self, judgment, operation_id):
            if self.fail_revert:
                raise OSError("disk offline")
            super().revert(judgment, operation_id)

    ledger = FlakyLedger()
    state = LearningState(generate_demo_artworks(2), ledger)
    loaded(state)
    generation = state.generation
    assert not judge_current(state, operation_id="retry")
    assert (state.index, state.ready, state.generation) == (0, True, generation)
    assert "disk offline" in state.last_error
    ledger.fail_record = False
    assert judge_current(state, operation_id="retry")
    assert not state.undo(operation_id="undo-retry")
    assert state.index == 1 and state.can_undo
    assert "disk offline" in state.last_error
    ledger.fail_revert = False
    assert state.undo(operation_id="undo-retry")
    assert state.index == 0 and not state.ready


def test_zero_items_last_item_close_and_independent_pages():
    empty = LearningState(())
    assert empty.current_artwork is None and not empty.ready
    assert not empty.can_undo and not empty.undo(operation_id="undo")
    with pytest.raises(RuntimeError, match="no current"):
        empty.begin_load()
    items = generate_demo_artworks(1)
    a, b = LearningState(items), LearningState(items)
    first = loaded(a)
    assert not b.ready and b.index == 0
    assert judge_current(a)
    assert a.current_artwork is None and b.current_artwork == items[0]
    assert a.undo(operation_id="u")
    assert not a.ready and not a.finish_load(first)
    pending = a.begin_load()
    a.close()
    assert a.closed and a.current_artwork is None
    assert not a.finish_load(pending)
    assert not a.judge("like", expected_work_id=items[0].work_id,
                       expected_generation=a.generation, operation_id="x")
    assert not a.undo(operation_id="v")
    with pytest.raises(RuntimeError):
        a.begin_load()
    assert loaded(b) and b.ready


def test_invalid_input_and_duplicate_artworks():
    item = generate_demo_artworks(1)[0]
    with pytest.raises(ValueError, match="duplicate work_id"):
        LearningState((item, item))
    state = LearningState((item,))
    loaded(state)
    assert not judge_current(state, "unknown")
    assert state.ready and state.index == 0 and state.last_error
    assert not judge_current(state, operation_id="")
    assert state.ready and state.index == 0


def test_original_one_step_undo_preserves_earlier_judgments():
    # Original discovery_tab.py:146-165/1988-1994: only the last in-memory move.
    state = LearningState(generate_demo_artworks(3))
    loaded(state)
    assert judge_current(state, operation_id="first")
    loaded(state)
    assert judge_current(state, operation_id="second")
    assert state.undo(operation_id="undo-second")
    assert not state.can_undo
    assert not state.undo(operation_id="undo-first")
    assert state.index == 1
    assert [entry.operation_id for entry in state.ledger.entries] == ["first"]


def test_pending_save_rejects_reentry_without_advancing_or_reloading():
    state = LearningState(generate_demo_artworks(2))
    loaded(state)
    command = state.prepare_judgment(
        "like", expected_work_id="demo-0001", expected_generation=state.generation,
        operation_id="save-1",
    )
    assert command is not None and state.saving
    assert state.index == 0 and not state.can_undo and not state.ledger.entries
    assert state.prepare_judgment(
        "hate", expected_work_id="demo-0001", expected_generation=state.generation,
        operation_id="save-2",
    ) is None
    assert state.prepare_undo(operation_id="undo-1") is None
    with pytest.raises(RuntimeError, match="save pending"):
        state.begin_load()
    assert state.complete_save(command)
    assert state.index == 1 and state.can_undo and not state.saving


def test_save_completion_requires_same_command_and_is_consumed_once():
    from dataclasses import replace

    state = LearningState(generate_demo_artworks(2))
    loaded(state)
    command = state.prepare_judgment(
        "like", expected_work_id="demo-0001", expected_generation=state.generation,
        operation_id="save-1",
    )
    assert not state.complete_save(replace(command))
    assert state.index == 0 and state.saving
    assert state.complete_save(command)
    assert not state.complete_save(command)
    assert state.index == 1


def test_async_save_and_undo_failures_preserve_state_for_same_id_retry():
    state = LearningState(generate_demo_artworks(2))
    loaded(state)
    generation = state.generation
    options = dict(expected_work_id="demo-0001", expected_generation=generation,
                   operation_id="retry-save")
    command = state.prepare_judgment("like", **options)
    assert not state.complete_save(command, OSError("disk offline"))
    assert (state.index, state.ready, state.generation, state.saving) == (0, True, generation, False)
    assert "disk offline" in state.last_error
    assert state.complete_save(state.prepare_judgment("like", **options))
    undo = state.prepare_undo(operation_id="retry-undo")
    assert not state.can_undo and state.index == 1
    assert not state.complete_save(undo, OSError("disk offline"))
    assert state.can_undo and state.index == 1
    assert state.complete_save(state.prepare_undo(operation_id="retry-undo"))
    assert state.index == 0 and not state.ready and not state.can_undo


@pytest.mark.parametrize("action", ["record", "undo"])
def test_completion_after_view_close_cannot_revive_state(action):
    state = LearningState(generate_demo_artworks(2))
    loaded(state)
    if action == "record":
        command = state.prepare_judgment(
            "like", expected_work_id="demo-0001", expected_generation=state.generation,
            operation_id="save-1",
        )
    else:
        assert judge_current(state)
        command = state.prepare_undo(operation_id="undo-1")
    index = state.index
    state.close()
    assert not state.complete_save(command)
    assert state.closed and state.current_artwork is None and state.index == index
    assert not state.can_undo and not state.saving
