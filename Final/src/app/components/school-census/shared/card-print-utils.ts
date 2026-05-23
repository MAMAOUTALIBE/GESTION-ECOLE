import { CensusPerson, PersonType } from './school-census.models';

export function printPersonCards(
  title: string,
  people: CensusPerson[],
  personType: PersonType,
  targetWindow?: Window | null,
) {
  const printWindow = targetWindow ?? window.open('', '_blank', 'width=960,height=720');
  if (!printWindow) {
    return;
  }

  const cards = people.map((person) => cardHtml(person, personType)).join('');

  printWindow.document.write(`
    <!doctype html>
    <html lang="fr">
      <head>
        <meta charset="utf-8" />
        <title>${escapeHtml(title)}</title>
        <style>${styles()}</style>
      </head>
      <body>
        <h1>${escapeHtml(title)}</h1>
        <main class="card-sheet">${cards}</main>
      </body>
    </html>
  `);
  printWindow.document.close();
  printWindow.focus();
  setTimeout(() => {
    printWindow.print();
    printWindow.close();
  }, 300);
}

export function openPrintWindow(title: string) {
  const printWindow = window.open('', '_blank', 'width=960,height=720');
  if (!printWindow) {
    return null;
  }

  printWindow.document.write(`
    <!doctype html>
    <html lang="fr">
      <head>
        <meta charset="utf-8" />
        <title>${escapeHtml(title)}</title>
      </head>
      <body style="font-family: Arial, sans-serif; padding: 24px; color: #1f2937;">
        Préparation des cartes...
      </body>
    </html>
  `);
  printWindow.document.close();
  return printWindow;
}

function cardHtml(person: CensusPerson, personType: PersonType) {
  const typeLabel = personType === 'STUDENT' ? 'Élève' : 'Enseignant';
  const cardTitle = personType === 'STUDENT' ? 'Carte scolaire' : 'Carte enseignant';
  const secondLine = personType === 'STUDENT' ? person.classRoom?.name : person.subject;
  const thirdLine = personType === 'STUDENT' ? person.classRoom?.schoolYear : person.classes?.map((item) => item.name).join(', ');
  const photo = person.photoUrl
    ? `<img class="school-id-card__photo" src="${escapeHtml(person.photoUrl)}" alt="${escapeHtml(person.fullName)}" />`
    : `<div class="school-id-card__avatar">${escapeHtml(initials(person))}</div>`;

  return `
    <section class="school-id-card">
      <div class="school-id-card__header">
        <div class="school-id-card__kicker">GESTION-EE</div>
        <div class="school-id-card__title">${escapeHtml(cardTitle)}</div>
      </div>
      <div class="school-id-card__body">
        ${photo}
        <div>
          <div class="school-id-card__name">${escapeHtml(person.fullName)}</div>
          <div class="school-id-card__meta">
            <div>${escapeHtml(typeLabel)}</div>
            <div>${escapeHtml(person.school.name)}</div>
            ${secondLine ? `<div>${escapeHtml(secondLine)}</div>` : ''}
            ${thirdLine ? `<div>${escapeHtml(thirdLine)}</div>` : ''}
          </div>
          <div class="school-id-card__code">${escapeHtml(person.uniqueCode)}</div>
        </div>
        <div class="school-id-card__qr">${person.qrSvg ?? ''}</div>
      </div>
      <div class="school-id-card__footer">
        <span>${escapeHtml(person.school.region?.name ?? 'Région non renseignée')}</span>
        <span>QR: matricule unique</span>
      </div>
    </section>
  `;
}

function initials(person: CensusPerson) {
  return `${person.firstName.charAt(0)}${person.lastName.charAt(0)}`.toUpperCase();
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function styles() {
  return `
    * { box-sizing: border-box; }
    body { margin: 0; padding: 18px; font-family: Arial, sans-serif; color: #1f2937; }
    h1 { font-size: 18px; margin: 0 0 14px; }
    .card-sheet { display: grid; grid-template-columns: repeat(2, 86mm); gap: 10mm 8mm; align-items: start; }
    .school-id-card { width: 86mm; min-height: 54mm; border: 1px solid #1f2937; border-radius: 8px; padding: 12px; background: #fff; break-inside: avoid; page-break-inside: avoid; }
    .school-id-card__header { border-bottom: 1px solid #d1d5db; padding-bottom: 8px; margin-bottom: 10px; }
    .school-id-card__kicker { color: #6b7280; font-size: 9px; text-transform: uppercase; letter-spacing: 0; }
    .school-id-card__title { font-size: 13px; font-weight: 700; margin-top: 2px; }
    .school-id-card__body { display: grid; grid-template-columns: 58px 1fr 78px; gap: 10px; align-items: center; }
    .school-id-card__photo, .school-id-card__avatar { width: 58px; height: 68px; border-radius: 6px; object-fit: cover; border: 1px solid #d1d5db; }
    .school-id-card__avatar { display: grid; place-items: center; font-size: 20px; font-weight: 700; color: #2563eb; background: #dbeafe; }
    .school-id-card__name { font-size: 14px; font-weight: 700; line-height: 1.2; overflow-wrap: anywhere; }
    .school-id-card__meta { margin-top: 5px; font-size: 10px; line-height: 1.45; color: #4b5563; }
    .school-id-card__code { margin-top: 6px; font-size: 10px; font-weight: 700; color: #111827; overflow-wrap: anywhere; }
    .school-id-card__qr svg { width: 76px; height: 76px; display: block; }
    .school-id-card__footer { margin-top: 10px; border-top: 1px solid #d1d5db; padding-top: 6px; font-size: 9px; color: #6b7280; display: flex; justify-content: space-between; gap: 8px; }
    @page { size: A4; margin: 10mm; }
  `;
}
