import { getAuthToken } from '@heyputer/puter.js/src/init.cjs';

try {
  const token = await getAuthToken();
  if (!token) {
    console.error('No auth token returned from Puter login flow');
    process.exit(1);
  }
  process.stdout.write(`${token}\n`);
} catch (error) {
  console.error(error?.message || String(error));
  process.exit(1);
}