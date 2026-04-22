import { init } from '@heyputer/puter.js/src/init.cjs';

const [, , model, prompt] = process.argv;
const authToken = process.env.PUTER_AUTH_TOKEN || process.env.puterAuthToken || '';

if (!model || !prompt) {
  console.error('usage: node scripts/puter_grok_chat.mjs <model> <prompt>');
  process.exit(2);
}

if (!authToken) {
  console.error('PUTER_AUTH_TOKEN is not set');
  process.exit(3);
}

const puter = init(authToken);

try {
  const response = await puter.ai.chat(prompt, { model });
  const text = response?.message?.content ?? response?.message ?? response?.text ?? '';
  process.stdout.write(String(text).trim());
} catch (error) {
  console.error(error?.message || String(error));
  process.exit(1);
}