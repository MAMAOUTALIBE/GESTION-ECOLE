import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { Operation } from '../shared/simulator-api.service';
import { OperationsPanel } from './operations-panel';

describe('OperationsPanel', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [OperationsPanel],
    });
  });

  it('rend des chips pour chaque opération et déclenche removeOperation', () => {
    const fixture = TestBed.createComponent(OperationsPanel);
    const ops: Operation[] = [
      {
        type: 'CREATE_SCHOOL',
        name: 'École X',
        lat: 9.5,
        lon: -13.7,
        capacity: 150,
      },
      { type: 'CLOSE_SCHOOL', schoolId: 'SCH-1' },
    ];
    fixture.componentRef.setInput('operations', ops);
    fixture.componentRef.setInput('schools', [
      {
        id: 'SCH-1',
        name: 'École Alpha',
        code: 'AAA',
        regionId: 'R-1',
      },
    ]);
    fixture.detectChanges();

    expect(fixture.componentInstance.hasOperations()).toBe(true);
    expect(fixture.componentInstance.labelFor(ops[0])).toContain('École X');
    expect(fixture.componentInstance.labelFor(ops[1])).toContain('École Alpha');

    const chips: NodeListOf<HTMLElement> =
      fixture.nativeElement.querySelectorAll('[data-testid="op-chip"]');
    expect(chips.length).toBe(2);

    let removedIndex: number | null = null;
    fixture.componentInstance.removeOperation.subscribe(
      (i: number) => (removedIndex = i),
    );
    fixture.componentInstance.onRemove(1);
    expect(removedIndex).toBe(1);
  });

  it('bloque compute / save quand aucune opération ou scénario non sauvegardé', () => {
    const fixture = TestBed.createComponent(OperationsPanel);
    fixture.componentRef.setInput('operations', []);
    fixture.componentRef.setInput('scenarioId', null);
    fixture.componentRef.setInput('canEdit', true);
    fixture.detectChanges();

    expect(fixture.componentInstance.canCompute()).toBe(false);
    expect(fixture.componentInstance.canSave()).toBe(false);

    // Avec ops mais sans scenarioId → save OK, compute KO.
    fixture.componentRef.setInput('operations', [
      { type: 'CLOSE_SCHOOL', schoolId: 'X' } as Operation,
    ]);
    fixture.detectChanges();
    expect(fixture.componentInstance.canSave()).toBe(true);
    expect(fixture.componentInstance.canCompute()).toBe(false);

    // Avec ops et scenarioId → compute OK.
    fixture.componentRef.setInput('scenarioId', 'SCN-1');
    fixture.detectChanges();
    expect(fixture.componentInstance.canCompute()).toBe(true);
  });

  it('confirmSave émet le nom et la description, ne rien émettre si nom vide', () => {
    const fixture = TestBed.createComponent(OperationsPanel);
    fixture.componentRef.setInput('operations', [
      { type: 'CLOSE_SCHOOL', schoolId: 'X' } as Operation,
    ]);
    fixture.componentRef.setInput('canEdit', true);
    fixture.detectChanges();

    let emitted: { name: string; description: string | null } | null = null;
    fixture.componentInstance.save.subscribe(
      (payload: { name: string; description: string | null }) => {
        emitted = payload;
      },
    );

    // Nom vide → pas d'émission.
    fixture.componentInstance.setDraftName('   ');
    fixture.componentInstance.confirmSave();
    expect(emitted).toBeNull();

    // Nom valide + description vide → description doit être null.
    fixture.componentInstance.setDraftName('Scénario A');
    fixture.componentInstance.setDraftDescription('  ');
    fixture.componentInstance.confirmSave();
    expect(emitted).toEqual({ name: 'Scénario A', description: null });

    // Description non vide → renvoyée trimmée.
    fixture.componentInstance.setDraftName('Scénario B');
    fixture.componentInstance.setDraftDescription('  hello world  ');
    fixture.componentInstance.confirmSave();
    expect(emitted).toEqual({
      name: 'Scénario B',
      description: 'hello world',
    });
  });
});
