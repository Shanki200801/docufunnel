/** The run loop.
 *
 *  Ordering that matters: an email is labelled only after its rows have
 *  reached the sheet. A run that dies partway therefore repeats the tail
 *  rather than losing it, and the duplicate check makes that repeat harmless.
 */

// Apps Script kills a consumer execution at 6 minutes. Stopping at 4.5 leaves
// room to finish the email in hand and write the log, so a run ends tidily
// instead of being cut off mid-write.
var MAX_RUN_MS = 4.5 * 60 * 1000;

function runPipeline(options) {
  options = options || {};
  var dryRun = !!options.dryRun;
  var scheduled = !!options.scheduled;
  var started = Date.now();

  // Overlapping runs would both pick up the same unlabelled mail. A trigger
  // firing while a manual run is going is the normal way that happens.
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) {
    logWarn_('Another run is already in progress; skipping this one.');
    if (!scheduled) alert_('Already running', 'A run is in progress. Try again in a minute.');
    return;
  }

  var stats = { emails: 0, attachments: 0, written: 0, skipped: 0, failed: 0 };
  var firstRecord = null;

  try {
    var settings = readSettings_();
    var fields = readFields_();
    var apiKey = getApiKey_();
    if (!apiKey) {
      throw new Error('No Gemini API key yet. Use ' + MENU_NAME + ' > Set up.');
    }

    var query = setting_(settings, 'Gmail search', '');
    if (!query) throw new Error('The "Gmail search" setting is empty.');

    var label = setting_(settings, 'Processed label', 'docufunnel-done');
    var nameFilter = setting_(settings, 'Attachment name filter', '*');
    var drivePath = setting_(settings, 'Save copies to Drive folder', '');
    var outputTab = setting_(settings, 'Output tab', 'Data');
    var keyField = setting_(settings, 'Duplicate check field', '');
    var maxEmails = parseInt(setting_(settings, 'Max emails per run', '20'), 10) || 20;
    var model = setting_(settings, 'Gemini model', 'gemini-2.5-flash');
    var minBytes = (parseFloat(setting_(settings, 'Minimum attachment size (KB)', '2')) || 0) * 1024;

    // Excluded server-side, so already-processed mail is never downloaded.
    var searchQuery = query + ' -label:' + label;
    logInfo_((dryRun ? 'TEST RUN — ' : '') + 'searching: ' + searchQuery);

    var threads = GmailApp.search(searchQuery, 0, maxEmails);
    if (!threads.length) {
      logInfo_('No matching email. Nothing to do.');
      if (!scheduled) {
        alert_('Nothing to do', 'No email matched:\n\n' + searchQuery +
          '\n\nIf you expected matches, try the search in Gmail first.');
      }
      return;
    }

    var gmailLabel = GmailApp.getUserLabelByName(label) || GmailApp.createLabel(label);
    var outOfTime = false;

    for (var t = 0; t < threads.length && !outOfTime; t++) {
      var messages = threads[t].getMessages();

      for (var m = 0; m < messages.length && !outOfTime; m++) {
        if (Date.now() - started > MAX_RUN_MS) {
          outOfTime = true;
          logWarn_('Stopping early to stay inside the time limit. Run again to continue.');
          break;
        }

        var message = messages[m];
        var attachments = message.getAttachments({ includeInlineImages: false });
        var records = [];

        for (var a = 0; a < attachments.length; a++) {
          var attachment = attachments[a];
          if (!globMatch_(attachment.getName(), nameFilter)) continue;
          if (attachment.getSize() < minBytes) continue;

          stats.attachments++;
          try {
            var driveLink = '';
            if (drivePath && !dryRun) {
              driveLink = saveToDrive_(attachment, drivePath, message.getDate());
            }

            var extracted = extractFromAttachment_(attachment, fields, model, apiKey);
            var record = {};
            for (var f = 0; f < fields.length; f++) {
              record[fields[f].name] = extracted[fields[f].name];
            }
            record._file = attachment.getName();
            record._from = message.getFrom();
            record._subject = message.getSubject();
            record._email_date = message.getDate();
            record._drive_link = driveLink;
            record._extracted_at = new Date();

            records.push(record);
            if (!firstRecord) firstRecord = record;
          } catch (err) {
            stats.failed++;
            logError_(attachment.getName() + ': ' + err.message);
          }
        }

        if (!records.length) continue;
        stats.emails++;

        if (dryRun) {
          stats.skipped += records.length;
          continue;
        }

        var written = appendRecords_(outputTab, records, fields, keyField);
        stats.written += written;
        stats.skipped += records.length - written;

        // Only now, with the rows safely in the sheet.
        message.getThread().addLabel(gmailLabel);
      }
    }

    var summary = 'emails ' + stats.emails + ', attachments ' + stats.attachments +
      ', rows written ' + stats.written + ', duplicates skipped ' + stats.skipped +
      ', failed ' + stats.failed;
    logInfo_((dryRun ? 'TEST RUN finished — nothing was saved. ' : 'Finished. ') + summary);

    if (!scheduled) {
      reportRun_(dryRun, stats, firstRecord, outputTab);
    }
  } catch (err) {
    logError_(err.message);
    if (!scheduled) alert_('Something went wrong', err.message);
    throw err;
  } finally {
    lock.releaseLock();
  }
}

function reportRun_(dryRun, stats, firstRecord, outputTab) {
  var lines = [];
  if (dryRun) {
    lines.push('This was a test run. Nothing was saved and no email was marked as done.');
    lines.push('');
  }
  lines.push('Emails read: ' + stats.emails);
  lines.push('Attachments processed: ' + stats.attachments);
  lines.push(dryRun ? 'Rows that would be written: ' + stats.skipped
                    : 'Rows written to "' + outputTab + '": ' + stats.written);
  if (!dryRun && stats.skipped) lines.push('Duplicates skipped: ' + stats.skipped);
  if (stats.failed) lines.push('Failed: ' + stats.failed + ' (see the Log tab)');

  if (firstRecord) {
    lines.push('');
    lines.push('First result:');
    for (var key in firstRecord) {
      if (key.charAt(0) === '_') continue;
      lines.push('  ' + key + ': ' + (firstRecord[key] === null ? '(not found)' : firstRecord[key]));
    }
  }

  if (dryRun && stats.attachments) {
    lines.push('');
    lines.push('Happy with that? Run ' + MENU_NAME + ' > Run now.');
  }
  alert_(dryRun ? 'Test run finished' : 'Run finished', lines.join('\n'));
}

function alert_(title, message) {
  try {
    SpreadsheetApp.getUi().alert(title, message, SpreadsheetApp.getUi().ButtonSet.OK);
  } catch (err) {
    // No UI available (a trigger, or the script editor); the log has it.
    console.log(title + ': ' + message);
  }
}
