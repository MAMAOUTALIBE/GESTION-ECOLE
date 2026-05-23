function h(t,d,n){let r=[n.map(e=>c(e.header)).join(";"),...d.map(e=>n.map(a=>c(a.value(e))).join(";"))];p(t,`\uFEFF${r.join(`
`)}`,"text/csv;charset=utf-8")}function m(t,d,n){let o=n.map(e=>`<th>${i(e.header)}</th>`).join(""),r=d.map(e=>`<tr>${n.map(a=>`<td>${i(l(a.value(e)))}</td>`).join("")}</tr>`).join("");p(t,`<!doctype html><html><head><meta charset="utf-8"></head><body><table><thead><tr>${o}</tr></thead><tbody>${r}</tbody></table></body></html>`,"application/vnd.ms-excel;charset=utf-8")}function u(t,d,n){let o=window.open("","_blank","width=960,height=720");if(!o)return;let r=n.map(a=>`<th>${i(a.header)}</th>`).join(""),e=d.map(a=>`<tr>${n.map(s=>`<td>${i(l(s.value(a)))}</td>`).join("")}</tr>`).join("");o.document.write(`
    <!doctype html>
    <html lang="fr">
      <head>
        <meta charset="utf-8" />
        <title>${i(t)}</title>
        <style>
          * { box-sizing: border-box; }
          body { margin: 0; padding: 24px; font-family: Arial, sans-serif; color: #111827; }
          h1 { font-size: 20px; margin: 0 0 16px; }
          table { width: 100%; border-collapse: collapse; font-size: 11px; }
          th, td { border: 1px solid #d1d5db; padding: 6px 8px; text-align: left; vertical-align: top; }
          th { background: #f3f4f6; font-weight: 700; }
          tr { break-inside: avoid; }
        </style>
      </head>
      <body>
        <h1>${i(t)}</h1>
        <table><thead><tr>${r}</tr></thead><tbody>${e}</tbody></table>
      </body>
    </html>
  `),o.document.close(),o.focus(),setTimeout(()=>{o.print(),o.close()},250)}function c(t){return`"${l(t).replace(/"/g,'""')}"`}function l(t){return t==null?"":String(t)}function i(t){return t.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#039;")}function p(t,d,n){let o=new Blob([d],{type:n}),r=URL.createObjectURL(o),e=document.createElement("a");e.href=r,e.download=t,document.body.appendChild(e),e.click(),document.body.removeChild(e),URL.revokeObjectURL(r)}export{h as a,m as b,u as c};
