import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { describe, expect, it, beforeEach } from 'vitest';
import * as L from 'leaflet';

import { GuineaMapService } from './guinea-map.service';

/**
 * Couvre la méthode `buildNeonMarkerIcon` ajoutée pour le restyling
 * "centre de pilotage" néon : couleur selon type d'école, taille selon
 * effectif élèves, classe d'animation selon niveau d'alerte.
 */
describe('GuineaMapService — buildNeonMarkerIcon', () => {
  let service: GuineaMapService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(GuineaMapService);
  });

  it('retourne une L.DivIcon avec wrapper "cs-marker-wrapper"', () => {
    const icon = service.buildNeonMarkerIcon('normal', 'PUBLIC', 100);
    expect(icon).toBeInstanceOf(L.DivIcon);
    const opts = icon.options as L.DivIconOptions;
    expect(opts.className).toBe('cs-marker-wrapper');
  });

  it('attribue la couleur verte aux écoles PUBLIC', () => {
    const icon = service.buildNeonMarkerIcon('normal', 'PUBLIC', 100);
    const html = (icon.options as L.DivIconOptions).html as string;
    expect(html).toContain('cs-marker-green');
  });

  it('attribue la couleur rouge aux écoles PRIVATE', () => {
    const icon = service.buildNeonMarkerIcon('normal', 'PRIVATE', 100);
    const html = (icon.options as L.DivIconOptions).html as string;
    expect(html).toContain('cs-marker-red');
  });

  it('attribue la couleur jaune aux écoles COMMUNITY', () => {
    const icon = service.buildNeonMarkerIcon('normal', 'COMMUNITY', 100);
    const html = (icon.options as L.DivIconOptions).html as string;
    expect(html).toContain('cs-marker-yellow');
  });

  it('default → vert pour type inconnu ou vide', () => {
    const icon = service.buildNeonMarkerIcon('normal', '', 0);
    const html = (icon.options as L.DivIconOptions).html as string;
    expect(html).toContain('cs-marker-green');
  });

  it('taille = 12px quand effectif ≤ 50', () => {
    const icon = service.buildNeonMarkerIcon('normal', 'PUBLIC', 30);
    const html = (icon.options as L.DivIconOptions).html as string;
    expect(html).toContain('--size:12px');
    expect((icon.options as L.DivIconOptions).iconSize).toEqual([12, 12]);
  });

  it('taille = 16px quand effectif ∈ ]50, 200]', () => {
    const icon = service.buildNeonMarkerIcon('normal', 'PUBLIC', 200);
    const html = (icon.options as L.DivIconOptions).html as string;
    expect(html).toContain('--size:16px');
  });

  it('taille = 22px quand effectif ∈ ]200, 500]', () => {
    const icon = service.buildNeonMarkerIcon('normal', 'PUBLIC', 500);
    const html = (icon.options as L.DivIconOptions).html as string;
    expect(html).toContain('--size:22px');
  });

  it('taille = 28px quand effectif > 500', () => {
    const icon = service.buildNeonMarkerIcon('normal', 'PUBLIC', 1200);
    const html = (icon.options as L.DivIconOptions).html as string;
    expect(html).toContain('--size:28px');
    expect((icon.options as L.DivIconOptions).iconSize).toEqual([28, 28]);
  });

  it('ajoute classe cs-marker-calm pour niveau normal (perf)', () => {
    const icon = service.buildNeonMarkerIcon('normal', 'PUBLIC', 100);
    const html = (icon.options as L.DivIconOptions).html as string;
    expect(html).toContain('cs-marker-calm');
  });

  it('ajoute classe cs-marker-alert-critical pour niveau critical', () => {
    const icon = service.buildNeonMarkerIcon('critical', 'PUBLIC', 100);
    const html = (icon.options as L.DivIconOptions).html as string;
    expect(html).toContain('cs-marker-alert-critical');
    expect(html).not.toContain('cs-marker-calm');
  });

  it('ajoute classe cs-marker-alert-warning pour niveau warning', () => {
    const icon = service.buildNeonMarkerIcon('warning', 'COMMUNITY', 100);
    const html = (icon.options as L.DivIconOptions).html as string;
    expect(html).toContain('cs-marker-alert-warning');
  });

  it('config map a tileUrl Carto Dark Matter et borderStyle néon', () => {
    expect(service.config.tileUrl).toContain('dark_nolabels');
    expect(service.config.borderStyle.color).toBe('#16e07a');
    expect(service.config.borderStyle.className).toBe('guinea-border-glow');
  });
});
