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


def test_undo_lifo_reloads_same_artwork_and_rejects_replays():
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
    assert state.index == 1 and "duplicate" in state.last_error
    loaded(state)
    assert judge_current(state, operation_id="c")
    assert state.undo(operation_id="undo-c")
    assert state.undo(operation_id="undo-a")
    assert state.index == 0 and not state.can_undo
    assert not state.undo(operation_id="unused")
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
