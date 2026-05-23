export interface ExportColumn<T> {
  header: string;
  value: (row: T) => unknown;
}

export function downloadCsv<T>(filename: string, rows: T[], columns: ExportColumn<T>[]) {
  const delimiter = ';';
  const csvRows = [
    columns.map((column) => csvCell(column.header)).join(delimiter),
    ...rows.map((row) => columns.map((column) => csvCell(column.value(row))).join(delimiter)),
  ];
  downloadBlob(filename, `\uFEFF${csvRows.join('\n')}`, 'text/csv;charset=utf-8');
}

export function downloadExcel<T>(filename: string, rows: T[], columns: ExportColumn<T>[]) {
  const headerCells = columns.map((column) => `<th>${escapeHtml(column.header)}</th>`).join('');
  const bodyRows = rows
    .map(
      (row) =>
        `<tr>${columns
          .map((column) => `<td>${escapeHtml(toText(column.value(row)))}</td>`)
          .join('')}</tr>`,
    )
    .join('');

  downloadBlob(
    filename,
    `<!doctype html><html><head><meta charset="utf-8"></head><body><table><thead><tr>${headerCells}</tr></thead><tbody>${bodyRows}</tbody></table></body></html>`,
    'application/vnd.ms-excel;charset=utf-8',
  );
}

export function printTable<T>(title: string, rows: T[], columns: ExportColumn<T>[]) {
  const printWindow = window.open('', '_blank', 'width=960,height=720');
  if (!printWindow) {
    return;
  }

  const headerCells = columns.map((column) => `<th>${escapeHtml(column.header)}</th>`).join('');
  const bodyRows = rows
    .map(
      (row) =>
        `<tr>${columns
          .map((column) => `<td>${escapeHtml(toText(column.value(row)))}</td>`)
          .join('')}</tr>`,
    )
    .join('');

  printWindow.document.write(`
    <!doctype html>
    <html lang="fr">
      <head>
        <meta charset="utf-8" />
        <title>${escapeHtml(title)}</title>
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
        <h1>${escapeHtml(title)}</h1>
        <table><thead><tr>${headerCells}</tr></thead><tbody>${bodyRows}</tbody></table>
      </body>
    </html>
  `);
  printWindow.document.close();
  printWindow.focus();
  setTimeout(() => {
    printWindow.print();
    printWindow.close();
  }, 250);
}

function csvCell(value: unknown) {
  return `"${toText(value).replace(/"/g, '""')}"`;
}

function toText(value: unknown) {
  if (value === null || value === undefined) {
    return '';
  }
  return String(value);
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function downloadBlob(filename: string, content: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
