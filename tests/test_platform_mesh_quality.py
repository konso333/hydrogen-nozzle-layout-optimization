"""Measured quality gates and checkpoint recovery, independent of Fluent."""
import json
from types import SimpleNamespace
import pytest

from nozzle_platform.mesh_quality import parse_quality, condition_mesh


def text(q,a):
    return f'\nMinimum Orthogonal Quality = {q} cell 3 at location (1e-3, 0, -2e-3)\nMaximum Aspect Ratio = {a}\n'


@pytest.mark.parametrize('value',['',text('nan',30),text(.2,'inf'),text(0,3),text(1.1,2),text(.2,.5)])
def test_incomplete_or_invalid_quality_reports_fail_closed(value):
    with pytest.raises(ValueError):parse_quality(value)


class FakeSession:
    def __init__(self,folder,sequence):
        self.folder=folder;self.sequence=iter(sequence);self.current=None;self.saved=None
        self.calls=[]
        (folder/'fluent.trn').write_text('')
        self.settings=SimpleNamespace(mesh=SimpleNamespace(check=lambda:None,quality=self.quality,
            polyhedra=SimpleNamespace(convert_domain=lambda:self.calls.append('convert'))),
            file=SimpleNamespace(write_case=self.write,read_case=self.read))
        self.tui=SimpleNamespace(mesh=SimpleNamespace(repair_improve=SimpleNamespace(
            allow_repair_at_boundaries=lambda value:self.calls.append(('boundary',value)),
            include_local_polyhedra_conversion_in_repair=lambda value:self.calls.append(('local',value)),
            improve_quality=lambda:self.calls.append('improve'))))
    def quality(self):
        if self.current is None:self.current=next(self.sequence)
        with (self.folder/'fluent.trn').open('a') as f:f.write(text(*self.current))
        self.current=None
    def write(self,**kw):
        self.saved=parse_quality((self.folder/'fluent.trn').read_text())
    def read(self,**kw):
        self.calls.append('restore')
        self.current=(self.saved['minimum_orthogonal_quality'],self.saved['maximum_aspect_ratio'])


def test_conversion_first_then_bounded_internal_improvement(tmp_path):
    s=FakeSession(tmp_path,[(.0047,39),(.093,43),(.055,41),(.14,34)])
    report=condition_mesh(s,tmp_path,lambda _:None)
    assert s.calls[0]=='convert' and s.calls.count('improve')==2
    assert ('boundary','yes') not in s.calls and ('local','yes') not in s.calls
    assert report['mesh_quality_accepted']
    assert report['minimum_fluent_orthogonal_quality']==.14
    assert not report['mesh_quality_history'][2]['retained_as_best']


def test_improved_orthogonality_cannot_hide_bad_aspect_ratio(tmp_path):
    s=FakeSession(tmp_path,[(.004,39),(.09,43),(.12,2504)])
    with pytest.raises(ValueError,match='网格质量未通过'):
        condition_mesh(s,tmp_path,lambda _:None)
    assert 'restore' in s.calls
    report=json.loads((tmp_path/'mesh_quality.json').read_text())
    assert report['maximum_fluent_aspect_ratio']==43
    assert report['minimum_fluent_orthogonal_quality']==.09
    assert report['best_mesh_restored'] and not report['mesh_quality_accepted']


def test_already_acceptable_tetra_needs_no_conversion(tmp_path):
    s=FakeSession(tmp_path,[(.2,20)])
    report=condition_mesh(s,tmp_path,lambda _:None)
    assert not s.calls and not report['polyhedral_conversion_performed']
