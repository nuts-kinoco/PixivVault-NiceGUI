"""Original ZIP precedence, size order and enumeration tie rule."""
import pytest
from pixivvault_nicegui.capacity_selection import choose_author_entry

def test_zip_precedes_folder_and_largest_ties_keep_first():
    entries=[('Old(12)','folder',500),('X(12).zip','zip',10),('Y(12).zip','zip',10),('Other(112).zip','zip',100)]
    assert choose_author_entry('12',entries)=='X(12).zip'
    assert choose_author_entry('12',[entries[0],('New(12)','folder',600)])=='New(12)'
    assert choose_author_entry('99',entries) is None

def test_bad_id_and_invalid_entry_are_rejected():
    with pytest.raises(ValueError):choose_author_entry('../12',[])
    with pytest.raises(ValueError):choose_author_entry('12',[('X(12)','folder',-1)])
