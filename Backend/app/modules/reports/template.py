"""Inline HTML template for the school report card (bulletin).

Kept as a Python f-string to avoid pulling Jinja2 just for this. Replace with
a templating engine if more complex layouts emerge.
"""
from typing import Any

BULLETIN_HTML = """\
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Bulletin scolaire — {full_name}</title>
<style>
  @page {{ size: A4; margin: 18mm; }}
  body {{ font-family: 'DejaVu Sans', Arial, sans-serif; font-size: 10pt; color: #222; }}
  header {{ display: flex; justify-content: space-between; border-bottom: 2px solid #014a91; padding-bottom: 8px; margin-bottom: 12px; }}
  h1 {{ font-size: 14pt; color: #014a91; margin: 0; }}
  .meta {{ font-size: 9pt; color: #555; }}
  .student-block {{ display: flex; justify-content: space-between; margin-bottom: 12px; }}
  .student-block .info p {{ margin: 2px 0; }}
  .qr-block {{ text-align: center; }}
  .qr-block img {{ width: 90px; height: 90px; }}
  .qr-block .code {{ font-size: 8pt; font-family: 'DejaVu Sans Mono', monospace; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 6px; }}
  th, td {{ border: 1px solid #cfd8e3; padding: 5px 7px; text-align: left; }}
  th {{ background: #eaf1fa; color: #014a91; }}
  td.score, th.score {{ text-align: right; width: 70px; }}
  .summary {{ margin-top: 14px; padding: 10px; background: #f4f8fc; border-left: 4px solid #014a91; }}
  .summary-row {{ display: flex; justify-content: space-between; }}
  .signatures {{ display: flex; justify-content: space-between; margin-top: 28px; }}
  .signatures .sig-box {{ width: 32%; border-top: 1px solid #888; padding-top: 4px; text-align: center; font-size: 9pt; }}
  footer {{ position: fixed; bottom: 8mm; left: 18mm; right: 18mm; text-align: center; font-size: 8pt; color: #777; border-top: 1px solid #ccc; padding-top: 4px; }}
</style>
</head>
<body>
<header>
  <div>
    <h1>République de Guinée — Ministère de l'Éducation</h1>
    <div class="meta">{school_name}</div>
    <div class="meta">{region_name} · {prefecture_name}</div>
  </div>
  <div class="meta" style="text-align: right;">
    <div><strong>Année scolaire :</strong> {school_year_name}</div>
    <div><strong>Période :</strong> {period_name}</div>
    <div><strong>Émis le :</strong> {issued_at}</div>
  </div>
</header>

<div class="student-block">
  <div class="info">
    <p><strong>Élève :</strong> {full_name}</p>
    <p><strong>Matricule :</strong> {unique_code}</p>
    <p><strong>Classe :</strong> {class_name}</p>
  </div>
  <div class="qr-block">
    <img src="data:image/png;base64,{qr_base64}" alt="QR vérification" />
    <div class="code">{verification_code}</div>
  </div>
</div>

<table>
  <thead>
    <tr>
      <th>Matière</th>
      <th class="score">Coef.</th>
      <th class="score">/Max</th>
      <th class="score">Note</th>
      <th>Appréciation</th>
    </tr>
  </thead>
  <tbody>
    {grade_rows}
  </tbody>
</table>

<div class="summary">
  <div class="summary-row">
    <strong>Moyenne générale</strong>
    <span><strong>{average} / 20</strong></span>
  </div>
  <div class="summary-row">
    <strong>Rang</strong>
    <span>{rank} / {total_students}</span>
  </div>
  <div class="summary-row">
    <strong>Statut</strong>
    <span>{status}</span>
  </div>
</div>

<div class="signatures">
  <div class="sig-box">Enseignant</div>
  <div class="sig-box">Directeur</div>
  <div class="sig-box">Parent / Tuteur</div>
</div>

<footer>
  Document officiel — vérifiable sur {verify_url} avec le code {verification_code}.
</footer>
</body>
</html>
"""


GRADE_ROW_HTML = (
    "<tr>"
    "<td>{subject}</td>"
    "<td class='score'>{coefficient}</td>"
    "<td class='score'>{max_score}</td>"
    "<td class='score'>{score}</td>"
    "<td>{appreciation}</td>"
    "</tr>"
)


def render_grade_rows(grades: list[dict[str, Any]]) -> str:
    if not grades:
        return (
            "<tr><td colspan='5' style='text-align:center; color:#888;'>"
            "Aucune note enregistrée pour cette période.</td></tr>"
        )
    return "\n".join(GRADE_ROW_HTML.format(**g) for g in grades)
