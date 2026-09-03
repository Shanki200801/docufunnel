/** Configuration, read from spreadsheet tabs.
 *
 *  The Settings and Fields tabs are this edition's equivalent of the Python
 *  edition's YAML. Putting them in the sheet matters: a non-technical user can
 *  edit a cell, and cannot break the script by mis-indenting it.
 */

var SETTINGS_TAB = 'Settings';
var FIELDS_TAB = 'Fields';
var PROP_API_KEY = 'GEMINI_API_KEY';

/** Setting key -> [default, help text]. The help text is written into the
 *  sheet beside each row, so the config explains itself in place. */
var SETTING_SPEC = [
  ['Gmail search', 'has:attachment filename:pdf (invoice OR receipt OR bill)',
   'Anything you can type into the Gmail search box.'],
  ['Attachment name filter', '*.pdf',
   'Only attachments matching this are processed. Use * as a wildcard.'],
  ['Save copies to Drive folder', 'DocuFunnel/{{yyyy-mm}}',
   'Leave blank to skip saving. {{yyyy-mm}} becomes e.g. 2026-09.'],
  ['Output tab', 'Data', 'Which tab the extracted rows are written to.'],
  ['Duplicate check field', 'invoice_no',
   'A row is skipped if this field already appears in the output tab. Use _file to check by attachment instead.'],
  ['Max emails per run', '20',
   'Keep this small at first. Runs stop after about 5 minutes regardless.'],
  ['Gemini model', 'gemini-2.5-flash', 'Leave as is unless you know you want another.'],
  ['Processed label', 'docufunnel-done',
   'Gmail label applied once an email is done, so it is not processed twice.'],
  ['Minimum attachment size (KB)', '2',
   'Skips signature images and tracking pixels that arrive as attachments.']
];

var FIELD_TYPES = ['text', 'number', 'whole number', 'date', 'yes/no'];

var SAMPLE_FIELDS = [
  ['invoice_no', 'text', 'The invoice or receipt number'],
  ['invoice_date', 'date', 'The date printed on the document'],
  ['vendor_name', 'text', 'The company that issued the document'],
  ['currency', 'text', 'Three-letter currency code, e.g. USD or INR'],
  ['total', 'number', 'The grand total including tax']
];

function sheetOrNull_(name) {
  return SpreadsheetApp.getActiveSpreadsheet().getSheetByName(name);
}

function readSettings_() {
  var sheet = sheetOrNull_(SETTINGS_TAB);
  if (!sheet) {
    throw new Error('No "' + SETTINGS_TAB + '" tab. Run ' + MENU_NAME + ' > Set up first.');
  }
  var values = sheet.getDataRange().getValues();
  var settings = {};
  for (var i = 1; i < values.length; i++) {
    var key = String(values[i][0]).trim();
    if (key) {
      settings[key] = String(values[i][1] === null ? '' : values[i][1]).trim();
    }
  }
  return settings;
}

function setting_(settings, key, fallback) {
  var value = settings[key];
  return value === undefined || value === '' ? fallback : value;
}

/** The Fields tab, as extraction targets.
 *
 *  The third column is the whole point: a plain-English description becomes
 *  the field's description in the JSON schema handed to the model. That is how
 *  someone with no code configures extraction.
 */
function readFields_() {
  var sheet = sheetOrNull_(FIELDS_TAB);
  if (!sheet) {
    throw new Error('No "' + FIELDS_TAB + '" tab. Run ' + MENU_NAME + ' > Set up first.');
  }
  var values = sheet.getDataRange().getValues();
  var fields = [];
  for (var i = 1; i < values.length; i++) {
    var name = String(values[i][0] || '').trim();
    if (!name) continue;
    fields.push({
      name: name,
      type: String(values[i][1] || 'text').trim().toLowerCase(),
      description: String(values[i][2] || '').trim()
    });
  }
  if (!fields.length) {
    throw new Error('The "' + FIELDS_TAB + '" tab is empty — add at least one field to extract.');
  }
  return fields;
}

function getApiKey_() {
  return PropertiesService.getScriptProperties().getProperty(PROP_API_KEY) || '';
}

function setApiKey_(key) {
  // Script properties, not a cell: the key is then not copied along when the
  // spreadsheet is shared or duplicated, so each person supplies their own.
  PropertiesService.getScriptProperties().setProperty(PROP_API_KEY, key);
}

/** Expand {{yyyy-mm}} style tokens in a Drive folder path. */
function renderPath_(template, when) {
  var tz = SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone();
  var d = when || new Date();
  return String(template)
    .replace(/\{\{\s*yyyy-mm\s*\}\}/gi, Utilities.formatDate(d, tz, 'yyyy-MM'))
    .replace(/\{\{\s*yyyy\s*\}\}/gi, Utilities.formatDate(d, tz, 'yyyy'))
    .replace(/\{\{\s*mm\s*\}\}/gi, Utilities.formatDate(d, tz, 'MM'))
    .replace(/\{\{\s*yyyy-mm-dd\s*\}\}/gi, Utilities.formatDate(d, tz, 'yyyy-MM-dd'));
}

/** Glob match for attachment filters, so users can write *.pdf rather than a
 *  regular expression. */
function globMatch_(name, pattern) {
  if (!pattern || pattern === '*') return true;
  var escaped = String(pattern)
    .replace(/[.+^${}()|[\]\\]/g, '\\$&')
    .replace(/\*/g, '.*')
    .replace(/\?/g, '.');
  return new RegExp('^' + escaped + '$', 'i').test(name);
}
