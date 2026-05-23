import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { ActivatedRoute, RouterModule } from '@angular/router';
import { CensusApiService } from '../shared/census-api.service';
import { CensusPerson, Gender, PersonType } from '../shared/school-census.models';

@Component({
  selector: 'app-person-profile',
  imports: [CommonModule, RouterModule],
  templateUrl: './person-profile.html',
  styleUrl: './person-profile.scss',
})
export class PersonProfile {
  private api = inject(CensusApiService);
  private route = inject(ActivatedRoute);
  private sanitizer = inject(DomSanitizer);

  person: CensusPerson | null = null;
  qrSvg: SafeHtml | null = null;
  personType: PersonType = 'STUDENT';
  loading = true;
  error = '';

  ngOnInit() {
    this.personType = (this.route.snapshot.data['personType'] as PersonType) ?? 'STUDENT';
    const id = this.route.snapshot.paramMap.get('id') ?? '';
    const request = this.personType === 'STUDENT' ? this.api.student(id) : this.api.teacher(id);

    request.subscribe({
      next: (person) => {
        this.person = person;
        this.qrSvg = person.qrSvg ? this.sanitizer.bypassSecurityTrustHtml(person.qrSvg) : null;
        this.loading = false;
      },
      error: () => {
        this.error = this.personType === 'STUDENT' ? 'Élève introuvable.' : 'Enseignant introuvable.';
        this.loading = false;
      },
    });
  }

  get listPath() {
    return this.personType === 'STUDENT' ? '/school-census/students' : '/school-census/teachers';
  }

  get typeLabel() {
    return this.personType === 'STUDENT' ? 'Élève' : 'Enseignant';
  }

  genderLabel(gender?: Gender) {
    const labels: Record<Gender, string> = {
      FEMALE: 'Féminin',
      MALE: 'Masculin',
      OTHER: 'Autre',
    };
    return gender ? labels[gender] : 'Non renseigné';
  }

  dateLabel(value?: string | null) {
    if (!value) {
      return 'Non renseignée';
    }
    return new Intl.DateTimeFormat('fr-FR').format(new Date(value));
  }

  printCard() {
    const card = document.getElementById('school-census-print-card');
    if (!card || !this.person) {
      return;
    }

    const printWindow = window.open('', '_blank', 'width=480,height=720');
    if (!printWindow) {
      return;
    }

    printWindow.document.write(`
      <!doctype html>
      <html lang="fr">
        <head>
          <meta charset="utf-8" />
          <title>Carte ${this.person.uniqueCode}</title>
          <style>${this.printStyles()}</style>
        </head>
        <body>${card.outerHTML}</body>
      </html>
    `);
    printWindow.document.close();
    printWindow.focus();
    setTimeout(() => {
      printWindow.print();
      printWindow.close();
    }, 250);
  }

  private printStyles() {
    return `
      * { box-sizing: border-box; }
      body { margin: 0; padding: 18px; font-family: Arial, sans-serif; color: #1f2937; }
      .school-id-card { width: 86mm; min-height: 54mm; border: 1px solid #1f2937; border-radius: 8px; padding: 12px; background: #fff; }
      .school-id-card__header { border-bottom: 1px solid #d1d5db; padding-bottom: 8px; margin-bottom: 10px; }
      .school-id-card__kicker { color: #6b7280; font-size: 9px; text-transform: uppercase; letter-spacing: 0; }
      .school-id-card__title { font-size: 13px; font-weight: 700; margin-top: 2px; }
      .school-id-card__body { display: grid; grid-template-columns: 58px 1fr 78px; gap: 10px; align-items: center; }
      .school-id-card__photo, .school-id-card__avatar { width: 58px; height: 68px; border-radius: 6px; object-fit: cover; border: 1px solid #d1d5db; }
      .school-id-card__avatar { display: grid; place-items: center; font-size: 20px; font-weight: 700; color: #2563eb; background: #dbeafe; }
      .school-id-card__name { font-size: 14px; font-weight: 700; line-height: 1.2; }
      .school-id-card__meta { margin-top: 5px; font-size: 10px; line-height: 1.45; color: #4b5563; }
      .school-id-card__code { margin-top: 6px; font-size: 10px; font-weight: 700; color: #111827; }
      .school-id-card__qr svg { width: 76px; height: 76px; display: block; }
      .school-id-card__footer { margin-top: 10px; border-top: 1px solid #d1d5db; padding-top: 6px; font-size: 9px; color: #6b7280; display: flex; justify-content: space-between; gap: 8px; }
    `;
  }
}
