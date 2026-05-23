function h(o,e,a,r){let t=r??window.open("","_blank","width=960,height=720");if(!t)return;let d=e.map(s=>n(s,a)).join("");t.document.write(`
    <!doctype html>
    <html lang="fr">
      <head>
        <meta charset="utf-8" />
        <title>${i(o)}</title>
        <style>${p()}</style>
      </head>
      <body>
        <h1>${i(o)}</h1>
        <main class="card-sheet">${d}</main>
      </body>
    </html>
  `),t.document.close(),t.focus(),setTimeout(()=>{t.print(),t.close()},300)}function m(o){let e=window.open("","_blank","width=960,height=720");return e?(e.document.write(`
    <!doctype html>
    <html lang="fr">
      <head>
        <meta charset="utf-8" />
        <title>${i(o)}</title>
      </head>
      <body style="font-family: Arial, sans-serif; padding: 24px; color: #1f2937;">
        Pr\xE9paration des cartes...
      </body>
    </html>
  `),e.document.close(),e):null}function n(o,e){let a=e==="STUDENT"?"\xC9l\xE8ve":"Enseignant",r=e==="STUDENT"?"Carte scolaire":"Carte enseignant",t=e==="STUDENT"?o.classRoom?.name:o.subject,d=e==="STUDENT"?o.classRoom?.schoolYear:o.classes?.map(c=>c.name).join(", "),s=o.photoUrl?`<img class="school-id-card__photo" src="${i(o.photoUrl)}" alt="${i(o.fullName)}" />`:`<div class="school-id-card__avatar">${i(l(o))}</div>`;return`
    <section class="school-id-card">
      <div class="school-id-card__header">
        <div class="school-id-card__kicker">GESTION-EE</div>
        <div class="school-id-card__title">${i(r)}</div>
      </div>
      <div class="school-id-card__body">
        ${s}
        <div>
          <div class="school-id-card__name">${i(o.fullName)}</div>
          <div class="school-id-card__meta">
            <div>${i(a)}</div>
            <div>${i(o.school.name)}</div>
            ${t?`<div>${i(t)}</div>`:""}
            ${d?`<div>${i(d)}</div>`:""}
          </div>
          <div class="school-id-card__code">${i(o.uniqueCode)}</div>
        </div>
        <div class="school-id-card__qr">${o.qrSvg??""}</div>
      </div>
      <div class="school-id-card__footer">
        <span>${i(o.school.region?.name??"R\xE9gion non renseign\xE9e")}</span>
        <span>QR: matricule unique</span>
      </div>
    </section>
  `}function l(o){return`${o.firstName.charAt(0)}${o.lastName.charAt(0)}`.toUpperCase()}function i(o){return o.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#039;")}function p(){return`
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
  `}export{h as a,m as b};
