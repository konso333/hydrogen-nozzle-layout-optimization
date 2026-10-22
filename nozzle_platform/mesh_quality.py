"""Bounded Fluent mesh conditioning; preserve the best measured candidate."""

import math
import re

from nozzle_platform.workflows import save


# Project screening limits for this isotropic, no-boundary-layer cold-flow
# template. These are not universal Fluent accuracy or convergence guarantees.
MINIMUM_ORTHOGONAL_QUALITY = .1
MAXIMUM_ASPECT_RATIO = 100.
MAXIMUM_IMPROVEMENT_PASSES = 4


def parse_quality(text):
    result = {}
    for label, key in [('Minimum Orthogonal Quality', 'minimum_orthogonal_quality'),
                       ('Maximum Aspect Ratio', 'maximum_aspect_ratio')]:
        matches = list(re.finditer(re.escape(label) + r'\s*=\s*(\S+)([^\r\n]*)', text))
        if not matches:
            raise ValueError('Fluent 没有返回完整的正交质量与长宽比报告。')
        match = matches[-1]
        try:
            value = float(match[1])
        except ValueError as error:
            raise ValueError('Fluent 网格质量数值无效。') from error
        if not math.isfinite(value) or value <= 0:
            raise ValueError('Fluent 网格质量数值无效。')
        result[key] = value
        location = re.search(r'at location\s*\(([^)]+)\)', match[2])
        if location:
            coordinates = [float(v.strip()) for v in location[1].split(',')]
            if len(coordinates) != 3 or not all(math.isfinite(v) for v in coordinates):
                raise ValueError('Fluent 劣质单元位置无效。')
            result[key + '_location_m'] = coordinates
    if result['minimum_orthogonal_quality'] > 1 or result['maximum_aspect_ratio'] < 1:
        raise ValueError('Fluent 网格质量超出有效范围。')
    return result


def acceptable(quality):
    return (quality['minimum_orthogonal_quality'] >= MINIMUM_ORTHOGONAL_QUALITY
            and quality['maximum_aspect_ratio'] <= MAXIMUM_ASPECT_RATIO)


def condition_mesh(session, directory, progress, *, preserve_layers=False, allow_conditioning=True):
    history = []
    best = None
    converted = False
    passes = 0
    limits = dict(minimum_orthogonal_quality=MINIMUM_ORTHOGONAL_QUALITY,
                  maximum_aspect_ratio=MAXIMUM_ASPECT_RATIO)

    def measure(stage):
        nonlocal best
        session.settings.mesh.check()
        transcript = directory / 'fluent.trn'
        offset = len(transcript.read_text(encoding='utf-8', errors='replace'))
        session.settings.mesh.quality()
        fresh = transcript.read_text(encoding='utf-8', errors='replace')[offset:]
        quality = parse_quality(fresh)
        quality.update(stage=stage, polyhedral_conversion_performed=converted, improvement_passes=passes)
        # Keep the most orthogonal candidate within the stretching budget.
        # A temporary decrease may be followed by an improvement on the next
        # pass; it must never overwrite the best checkpoint.
        selected = (quality['maximum_aspect_ratio'] <= MAXIMUM_ASPECT_RATIO
                    and (best is None or quality['minimum_orthogonal_quality'] > best['minimum_orthogonal_quality']))
        if selected:
            if allow_conditioning:session.settings.file.write_case(file_name='best_mesh.cas.h5')
            best = dict(quality)
        history.append(dict(quality, retained_as_best=selected and allow_conditioning))
        save(directory / 'quality_history.json', dict(limits=limits, stages=history, best=best))
        return quality

    initial = measure('imported_mixed' if preserve_layers else 'imported_tetra')
    current = initial
    if (preserve_layers or not allow_conditioning) and not acceptable(initial):
        save(directory/'mesh_quality.json',dict(mesh_quality_accepted=False,
            minimum_fluent_orthogonal_quality=initial['minimum_orthogonal_quality'],
            maximum_fluent_aspect_ratio=initial['maximum_aspect_ratio'],mesh_quality_limits=limits,
            mesh_quality_history=history))
        raise ValueError('壁面层网格导入后未通过质量门限；请重新生成网格。')
    if not acceptable(current):
        progress('正在直接转换多面体，联合检查正交质量与长宽比')
        session.settings.mesh.polyhedra.convert_domain()
        converted = True
        current = measure('direct_polyhedra')
    while not acceptable(current) and passes < MAXIMUM_IMPROVEMENT_PASSES:
        session.tui.mesh.repair_improve.allow_repair_at_boundaries('no')
        session.tui.mesh.repair_improve.include_local_polyhedra_conversion_in_repair('no')
        passes += 1
        progress(f'正在检查第 {passes} 次内部改善；保留最佳网格，边界固定')
        session.tui.mesh.repair_improve.improve_quality()
        current = measure('interior_' + str(passes))
        if current['maximum_aspect_ratio'] > MAXIMUM_ASPECT_RATIO:
            break
    restored = False
    if best is not None and current['stage'] != best['stage']:
        saved_best = dict(best)
        session.settings.file.read_case(file_name='best_mesh.cas.h5')
        restored = True
        current = measure('restored_best')
        # Reading a case must recover the saved quality, not just its label.
        if not all(math.isclose(current[key],saved_best[key],rel_tol=1e-5) for key in
                   ('minimum_orthogonal_quality','maximum_aspect_ratio')):
            raise ValueError('最佳网格恢复后的质量核对失败。')
        current.update(polyhedral_conversion_performed=saved_best['polyhedral_conversion_performed'],
                       improvement_passes=saved_best['improvement_passes'])
        best = saved_best
    chosen = best or current
    result = dict(imported_mesh_minimum_orthogonal_quality=initial['minimum_orthogonal_quality'],
                  imported_mesh_maximum_aspect_ratio=initial['maximum_aspect_ratio'],
                  minimum_fluent_orthogonal_quality=current['minimum_orthogonal_quality'],
                  maximum_fluent_aspect_ratio=current['maximum_aspect_ratio'],
                  polyhedral_conversion_performed=chosen['polyhedral_conversion_performed'],
                  polyhedral_conversion_type='whole_domain' if chosen['polyhedral_conversion_performed'] else None,
                  interior_improvement_passes=chosen['improvement_passes'], attempted_improvement_passes=passes,
                  best_mesh_restored=restored, mesh_quality_limits=limits, mesh_quality_history=history,
                  mesh_quality_accepted=acceptable(current))
    if not preserve_layers:
        result.update(imported_tetra_minimum_orthogonal_quality=initial['minimum_orthogonal_quality'],
            imported_tetra_maximum_aspect_ratio=initial['maximum_aspect_ratio'])
    save(directory / 'mesh_quality.json', result)
    if not result['mesh_quality_accepted']:
        raise ValueError(f'网格质量未通过：最小正交质量 {current["minimum_orthogonal_quality"]:.5g}（需 ≥ 0.1），'
                         f'最大长宽比 {current["maximum_aspect_ratio"]:.5g}（需 ≤ 100）。已停止求解，请重新生成网格。')
    return result
