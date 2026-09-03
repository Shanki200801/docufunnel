/** Gemini extraction over the REST API.
 *
 *  The attachment is sent as bytes, not as converted text. Gemini reads a PDF
 *  natively, which keeps column alignment and stamps intact and handles
 *  scanned pages without an OCR step — the two things a text-layer converter
 *  loses. It costs roughly 258 tokens per page, which the free tier absorbs
 *  for the volumes this edition is for.
 */

var GEMINI_HOST = 'https://generativelanguage.googleapis.com/v1beta';

var EXTRACT_PROMPT =
  'Extract the requested fields from this document. ' +
  'Use null for any field that is genuinely absent — never invent or guess a value. ' +
  'Return numbers as numbers, with currency symbols and thousands separators removed. ' +
  'Return dates as YYYY-MM-DD.';

/** Field type as written in the Fields tab -> JSON schema type.
 *  The REST API expects the enum name in upper case. */
function schemaTypeFor_(type) {
  switch (String(type).toLowerCase()) {
    case 'number':
    case 'amount':
    case 'decimal':
      return { type: 'NUMBER' };
    case 'whole number':
    case 'integer':
    case 'count':
      return { type: 'INTEGER' };
    case 'yes/no':
    case 'boolean':
    case 'true/false':
      return { type: 'BOOLEAN' };
    case 'date':
      return { type: 'STRING', description: 'ISO 8601 date, YYYY-MM-DD' };
    default:
      return { type: 'STRING' };
  }
}

function buildSchema_(fields) {
  var properties = {};
  var required = [];
  for (var i = 0; i < fields.length; i++) {
    var field = fields[i];
    var node = schemaTypeFor_(field.type);
    // The user's plain-English note wins over the generated hint: it is the
    // only place they get to say what the field actually means.
    if (field.description) {
      node.description = field.description;
    }
    node.nullable = true;
    properties[field.name] = node;
    required.push(field.name);
  }
  return { type: 'OBJECT', properties: properties, required: required };
}

/** True if the key is accepted. Uses models.list because it is free and does
 *  not consume quota, unlike a throwaway generation. */
function verifyApiKey_(key) {
  var response = UrlFetchApp.fetch(GEMINI_HOST + '/models?key=' + encodeURIComponent(key), {
    method: 'get',
    muteHttpExceptions: true
  });
  return response.getResponseCode() === 200;
}

function extractFromAttachment_(attachment, fields, model, apiKey) {
  var body = {
    contents: [{
      parts: [
        {
          inlineData: {
            mimeType: attachment.getContentType(),
            data: Utilities.base64Encode(attachment.getBytes())
          }
        },
        { text: EXTRACT_PROMPT }
      ]
    }],
    generationConfig: {
      responseMimeType: 'application/json',
      responseSchema: buildSchema_(fields),
      temperature: 0
    }
  };

  var url = GEMINI_HOST + '/models/' + encodeURIComponent(model) +
            ':generateContent?key=' + encodeURIComponent(apiKey);

  var response = fetchWithBackoff_(url, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(body),
    muteHttpExceptions: true
  });

  var code = response.getResponseCode();
  var text = response.getContentText();
  if (code !== 200) {
    throw new Error('Gemini returned ' + code + ': ' + text.slice(0, 300));
  }

  var parsed = JSON.parse(text);
  var candidates = parsed.candidates || [];
  if (!candidates.length || !candidates[0].content || !candidates[0].content.parts) {
    // A safety block or an empty candidate list arrives as a 200, so this is
    // a normal outcome rather than an exceptional one.
    var reason = (candidates[0] && candidates[0].finishReason) || parsed.promptFeedback
      ? JSON.stringify(candidates[0] ? candidates[0].finishReason : parsed.promptFeedback)
      : 'no candidates';
    throw new Error('Gemini returned no content (' + reason + ')');
  }
  return JSON.parse(candidates[0].content.parts[0].text);
}

/** Retry on the transient failures a free-tier key actually hits: rate limits
 *  and brief server errors. Anything else fails immediately, because retrying
 *  a bad request just wastes the run's time budget. */
function fetchWithBackoff_(url, params) {
  var attempts = 3;
  var response = null;
  for (var i = 0; i < attempts; i++) {
    response = UrlFetchApp.fetch(url, params);
    var code = response.getResponseCode();
    if (code !== 429 && code < 500) {
      return response;
    }
    if (i < attempts - 1) {
      Utilities.sleep(Math.pow(2, i) * 2000);
    }
  }
  return response;
}
