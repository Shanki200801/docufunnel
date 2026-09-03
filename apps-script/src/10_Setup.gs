/** First-run setup, scheduling, and a self-check.
 *
 *  Setup writes the config tabs with working defaults and a help column, so
 *  the spreadsheet explains itself without anyone reading a manual. The only
 *  thing it has to ask for is a Gemini API key — Gmail, Drive and Sheets
 *  access come from the fact that the script runs as the signed-in user.
 */

var TRIGGER_FUNCTION = 'scheduledRun';

function runSetup() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();

  ensureSettingsTab_(ss);
  ensureFieldsTab_(ss);
  logSheet_();

  var settings = readSettings_();
  outputSheet_(setting_(settings, 'Output tab', 'Data'));

  var key = getApiKey_();
  if (!key) {
    if (!promptForApiKey(false)) {
      return;
    }
  }

  logInfo_('Setup complete.');
  alert_('Ready', [
    'Two tabs to look at:',
    '',
    '  Settings — which emails to look at, where to put things.',
    '  Fields   — what to pull out of each document. The third column is a',
    '             plain-English note telling the AI what the field means.',
    '',
    'Then run ' + MENU_NAME + ' > "Test run". It reads your mail and shows',
    'what it found without saving anything or marking any email as done.',
    '',
    'When the results look right, use "Run now", then',
    '"Run automatically every hour".'
  ].join('\n'));

  ss.setActiveSheet(ss.getSheetByName(FIELDS_TAB));
}

function ensureSettingsTab_(ss) {
  var sheet = ss.getSheetByName(SETTINGS_TAB);
  if (sheet) return sheet;

  sheet = ss.insertSheet(SETTINGS_TAB, 0);
  var rows = [['Setting', 'Value', 'What this does']];
  for (var i = 0; i < SETTING_SPEC.length; i++) {
    rows.push(SETTING_SPEC[i]);
  }
  sheet.getRange(1, 1, rows.length, 3).setValues(rows);
  sheet.getRange(1, 1, 1, 3).setFontWeight('bold');
  sheet.setFrozenRows(1);
  sheet.setColumnWidth(1, 220);
  sheet.setColumnWidth(2, 340);
  sheet.setColumnWidth(3, 460);
  // The help column is reference material, not something to edit.
  sheet.getRange(2, 3, rows.length - 1, 1).setFontColor('#666666').setWrap(true);
  return sheet;
}

function ensureFieldsTab_(ss) {
  var sheet = ss.getSheetByName(FIELDS_TAB);
  if (sheet) return sheet;

  sheet = ss.insertSheet(FIELDS_TAB, 1);
  var rows = [['Field name', 'Type', 'What to look for (plain English)']];
  for (var i = 0; i < SAMPLE_FIELDS.length; i++) {
    rows.push(SAMPLE_FIELDS[i]);
  }
  sheet.getRange(1, 1, rows.length, 3).setValues(rows);
  sheet.getRange(1, 1, 1, 3).setFontWeight('bold');
  sheet.setFrozenRows(1);
  sheet.setColumnWidth(1, 200);
  sheet.setColumnWidth(2, 130);
  sheet.setColumnWidth(3, 480);
  sheet.getRange(2, 3, rows.length - 1, 1).setWrap(true);

  // A dropdown, so nobody has to guess what a valid type is.
  var validation = SpreadsheetApp.newDataValidation()
    .requireValueInList(FIELD_TYPES, true)
    .setAllowInvalid(false)
    .setHelpText('Pick one: ' + FIELD_TYPES.join(', '))
    .build();
  sheet.getRange(2, 2, 200, 1).setDataValidation(validation);

  sheet.getRange(rows.length + 2, 1).setValue('Add your own rows above. Delete any you do not need.')
    .setFontColor('#666666').setFontStyle('italic');
  return sheet;
}

/**
 * Ask for the Gemini API key and store it.
 * @return {boolean} whether a valid key is now stored.
 */
function promptForApiKey(isChange) {
  var ui = SpreadsheetApp.getUi();
  var response = ui.prompt(
    isChange ? 'Change Gemini API key' : 'One thing to paste',
    [
      'DocuFunnel needs a Gemini API key to read your documents.',
      '',
      'Get one free, in about 30 seconds:',
      '  1. Open  https://aistudio.google.com/apikey',
      '  2. Click "Create API key"',
      '  3. Copy it and paste it below',
      '',
      'Nothing else needs setting up — this spreadsheet already has',
      'permission to read your mail and write here.'
    ].join('\n'),
    ui.ButtonSet.OK_CANCEL
  );

  if (response.getSelectedButton() !== ui.Button.OK) {
    return false;
  }

  var key = response.getResponseText().trim();
  if (!key) {
    alert_('No key entered', 'Run ' + MENU_NAME + ' > Set up again when you have one.');
    return false;
  }

  // Check it before storing, so a typo surfaces here rather than as a failed
  // run an hour later.
  if (!verifyApiKey_(key)) {
    alert_('That key was not accepted',
      'Google rejected the key. Check for a stray space, and that it was created at\n' +
      'https://aistudio.google.com/apikey');
    return false;
  }

  setApiKey_(key);
  logInfo_('Gemini API key stored.');
  if (isChange) alert_('Saved', 'The new key works.');
  return true;
}

function createSchedule(kind) {
  removeTriggers_();
  var builder = ScriptApp.newTrigger(TRIGGER_FUNCTION).timeBased();
  if (kind === 'daily') {
    builder.everyDays(1).atHour(7).create();
  } else {
    builder.everyHours(1).create();
  }
  logInfo_('Automatic runs enabled (' + kind + ').');
  alert_('Scheduled', kind === 'daily'
    ? 'DocuFunnel will run once a day, around 7am in this spreadsheet\'s timezone.'
    : 'DocuFunnel will run about once an hour.\n\nStop it any time with "Stop running automatically".');
}

function menuUnschedule() {
  var removed = removeTriggers_();
  logInfo_('Automatic runs disabled (' + removed + ' trigger(s) removed).');
  alert_('Stopped', removed
    ? 'DocuFunnel will not run on its own any more. "Run now" still works.'
    : 'It was not running automatically anyway.');
}

function removeTriggers_() {
  var triggers = ScriptApp.getProjectTriggers();
  var removed = 0;
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === TRIGGER_FUNCTION) {
      ScriptApp.deleteTrigger(triggers[i]);
      removed++;
    }
  }
  return removed;
}

function scheduleDescription_() {
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === TRIGGER_FUNCTION) {
      return 'on';
    }
  }
  return 'off';
}

/** Answers "why did nothing happen" without opening the script editor. */
function showDoctor() {
  var lines = [];
  var problems = 0;

  var key = getApiKey_();
  if (!key) {
    lines.push('Gemini API key:  MISSING  — run Set up');
    problems++;
  } else if (!verifyApiKey_(key)) {
    lines.push('Gemini API key:  REJECTED — use "Change Gemini API key"');
    problems++;
  } else {
    lines.push('Gemini API key:  ok');
  }

  var tabs = [SETTINGS_TAB, FIELDS_TAB];
  for (var i = 0; i < tabs.length; i++) {
    if (sheetOrNull_(tabs[i])) {
      lines.push('Tab "' + tabs[i] + '":  ok');
    } else {
      lines.push('Tab "' + tabs[i] + '":  MISSING — run Set up');
      problems++;
    }
  }

  try {
    var settings = readSettings_();
    var fields = readFields_();
    lines.push('Fields to extract:  ' + fields.length);

    var query = setting_(settings, 'Gmail search', '');
    var label = setting_(settings, 'Processed label', 'docufunnel-done');
    if (!query) {
      lines.push('Gmail search:  EMPTY — fill it in on the Settings tab');
      problems++;
    } else {
      var waiting = GmailApp.search(query + ' -label:' + label, 0, 50).length;
      lines.push('Emails waiting:  ' + waiting + (waiting === 50 ? '+' : ''));
      if (!waiting) {
        lines.push('  (none matched — try the same search in Gmail to check it)');
      }
    }
  } catch (err) {
    lines.push('Config problem:  ' + err.message);
    problems++;
  }

  lines.push('Automatic runs:  ' + scheduleDescription_());
  lines.push('');
  lines.push(problems ? problems + ' thing(s) to fix.' : 'Everything looks fine.');
  alert_('Setup check', lines.join('\n'));
}
