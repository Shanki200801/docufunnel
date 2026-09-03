/** Run log, written to a sheet tab.
 *
 *  Apps Script's own execution log is invisible to anyone who did not open
 *  the script editor, which is exactly the audience this edition targets. So
 *  the log lives in the spreadsheet where they can read it.
 */

var LOG_TAB = 'Log';
var LOG_MAX_ROWS = 500;

function logSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(LOG_TAB);
  if (!sheet) {
    sheet = ss.insertSheet(LOG_TAB);
    sheet.getRange('A1:C1').setValues([['When', 'Level', 'Message']]);
    sheet.getRange('A1:C1').setFontWeight('bold');
    sheet.setFrozenRows(1);
    sheet.setColumnWidth(1, 150);
    sheet.setColumnWidth(3, 700);
  }
  return sheet;
}

function logWrite_(level, message) {
  try {
    var sheet = logSheet_();
    sheet.insertRowAfter(1);
    sheet.getRange(2, 1, 1, 3).setValues([[new Date(), level, String(message)]]);
    // Trim from the bottom so the newest entry is always the first data row.
    var rows = sheet.getLastRow();
    if (rows > LOG_MAX_ROWS) {
      sheet.deleteRows(LOG_MAX_ROWS + 1, rows - LOG_MAX_ROWS);
    }
  } catch (err) {
    // A logging failure must never abort a run.
    console.error('log failed: ' + err);
  }
  console.log(level + ': ' + message);
}

function logInfo_(message) {
  logWrite_('info', message);
}

function logWarn_(message) {
  logWrite_('warning', message);
}

function logError_(message) {
  logWrite_('error', message);
}
