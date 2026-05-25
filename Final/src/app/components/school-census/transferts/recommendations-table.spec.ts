import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { RecommendationsTable } from './recommendations-table';
import {
  RecommendationStatus,
  TeacherTransferRecommendation,
} from '../shared/staffing-api.service';

function makeReco(
  status: RecommendationStatus,
  id = 'REC-1',
): TeacherTransferRecommendation {
  return {
    id,
    schoolYearId: 'SY-2026',
    fromSchoolId: 'SCH-A',
    toSchoolId: 'SCH-B',
    prefectureId: null,
    regionId: 'REG-1',
    transfersSuggested: 2,
    priorityScore: '0.85',
    rationale: null,
    status,
    createdAt: '2026-05-01T00:00:00Z',
    reviewedById: null,
    reviewedAt: null,
    reviewNote: null,
  };
}

describe('RecommendationsTable', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [RecommendationsTable],
    });
  });

  it('expose 1 transition autorisée pour PENDING (Marquer revue)', () => {
    const fixture = TestBed.createComponent(RecommendationsTable);
    fixture.componentRef.setInput('recommendations', [makeReco('PENDING')]);
    fixture.componentRef.setInput('canReview', true);
    fixture.detectChanges();
    const transitions = fixture.componentInstance.allowedTransitions('PENDING');
    expect(transitions).toHaveLength(1);
    expect(transitions[0].status).toBe('REVIEWED');
  });

  it('expose 2 transitions (ACCEPTED/REJECTED) pour REVIEWED', () => {
    const fixture = TestBed.createComponent(RecommendationsTable);
    fixture.componentRef.setInput('recommendations', [makeReco('REVIEWED')]);
    fixture.componentRef.setInput('canReview', true);
    fixture.detectChanges();
    const transitions = fixture.componentInstance.allowedTransitions('REVIEWED');
    expect(transitions.map((t) => t.status).sort()).toEqual([
      'ACCEPTED',
      'REJECTED',
    ]);
  });

  it('aucune transition possible pour EXECUTED ou REJECTED (terminal)', () => {
    const fixture = TestBed.createComponent(RecommendationsTable);
    fixture.componentRef.setInput('recommendations', [makeReco('EXECUTED')]);
    fixture.componentRef.setInput('canReview', true);
    fixture.detectChanges();
    expect(
      fixture.componentInstance.allowedTransitions('EXECUTED'),
    ).toHaveLength(0);
    expect(
      fixture.componentInstance.allowedTransitions('REJECTED'),
    ).toHaveLength(0);
  });

  it('confirmAction emit le bon event avec reviewNote trimé', () => {
    const fixture = TestBed.createComponent(RecommendationsTable);
    fixture.componentRef.setInput('recommendations', [makeReco('REVIEWED')]);
    fixture.componentRef.setInput('canReview', true);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    let captured:
      | {
          recommendationId: string;
          targetStatus: string;
          reviewNote: string | null;
        }
      | null = null;
    component.review.subscribe((evt) => (captured = evt));

    component.openConfirm(component.rows()[0], 'ACCEPTED');
    component.reviewNote.set('  Validation conseil régional  ');
    component.confirmAction();

    expect(captured).not.toBeNull();
    expect(captured!.recommendationId).toBe('REC-1');
    expect(captured!.targetStatus).toBe('ACCEPTED');
    expect(captured!.reviewNote).toBe('Validation conseil régional');
    // Modal fermée après confirmation
    expect(component.pendingAction()).toBeNull();
  });
});
