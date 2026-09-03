/** Writing rows to the output tab.
 *
 *  The header row is treated as the user's, not the script's: they may have
 *  renamed or reordered columns, so rows are mapped onto whatever order the
 *  sheet already has and new fields are appended on the right. Nothing is
 *  ever shuffled underneath them.
 */

var PROVENANCE_COLUMNS = ['_file', '_from', '_subject', '_email_date', '_drive_link', '_extracted_at'];

function outputSheet_(name) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(name);
  if (!sheet) {
    sheet = ss.insertSheet(name);
  }
  return sheet;
}

function readHeader_(sheet) {
  if (sheet.getLastRow() === 0 || sheet.getLastColumn() === 0) {
    return [];
  }
  return sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0]
    .map(function (h) { return String(h).trim(); })
    .filter(function (h) { return h !== ''; });
}

function writeHeader_(sheet, header) {
  sheet.getRange(1, 1, 1, header.length).setValues([header]);
  sheet.getRange(1, 1, 1, header.length).setFontWeight('bold');
  sheet.setFrozenRows(1);
}

/** Values already present in the duplicate-check column.
 *
 *  Reads one column rather than the whole sheet, so a re-run against a few
 *  thousand rows stays cheap.
 */
function existingKeys_(sheet, header, keyField) {
  var keys = {};
  if (!keyField) return keys;
  var index = header.indexOf(keyField);
  if (index === -1 || sheet.getLastRow() < 2) return keys;

  var column = sheet.getRange(2, index + 1, sheet.getLastRow() - 1, 1).getValues();
  for (var i = 0; i < column.length; i++) {
    var value = column[i][0];
    if (value !== '' && value !== null) {
      keys[String(value)] = true;
    }
  }
  return keys;
}

function cellValue_(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') return JSON.stringify(value);
  return value;
}

/**
 * Appends records, skipping duplicates and widening the header if the Fields
 * tab has gained a column since the last run.
 *
 * @return {number} rows actually written.
 */
function appendRecords_(tabName, records, fields, keyField) {
  if (!records.length) return 0;

  var sheet = outputSheet_(tabName);
  var header = readHeader_(sheet);

  if (!header.length) {
    header = fields.map(function (f) { return f.name; }).concat(PROVENANCE_COLUMNS);
    writeHeader_(sheet, header);
  } else {
    var added = [];
    for (var i = 0; i < records.length; i++) {
      for (var key in records[i]) {
        if (header.indexOf(key) === -1 && added.indexOf(key) === -1) {
          added.push(key);
        }
      }
    }
    if (added.length) {
      header = header.concat(added);
      writeHeader_(sheet, header);
    }
  }

  var seen = existingKeys_(sheet, header, keyField);
  var rows = [];
  for (var r = 0; r < records.length; r++) {
    var record = records[r];
    if (keyField) {
      var key = record[keyField];
      if (key !== undefined && key !== null && key !== '') {
        if (seen[String(key)]) continue;
        seen[String(key)] = true;
      }
    }
    rows.push(header.map(function (column) { return cellValue_(record[column]); }));
  }

  if (!rows.length) return 0;
  sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, header.length).setValues(rows);
  return rows.length;
}

/** Resolve or create a nested Drive folder path, one segment at a time. */
function folderFromPath_(path) {
  var segments = String(path).split('/').filter(function (s) { return s.trim() !== ''; });
  var folder = DriveApp.getRootFolder();
  for (var i = 0; i < segments.length; i++) {
    var name = segments[i].trim();
    var matches = folder.getFoldersByName(name);
    folder = matches.hasNext() ? matches.next() : folder.createFolder(name);
  }
  return folder;
}

/** Saves the attachment, or returns the existing file's URL if a file of that
 *  name is already there — a re-run should not create a second copy. */
function saveToDrive_(attachment, pathTemplate, when) {
  var folder = folderFromPath_(renderPath_(pathTemplate, when));
  var existing = folder.getFilesByName(attachment.getName());
  if (existing.hasNext()) {
    return existing.next().getUrl();
  }
  return folder.createFile(attachment.copyBlob()).getUrl();
}
