require('./figma-mock.js');
const path = require.resolve('../code.js');

async function pasada(n) {
  delete require.cache[path];
  global.__CLOSE_MSG = null;
  require('../code.js');
  await new Promise(r => setTimeout(r, 300));
  console.log(`--- PASADA ${n} ---\n  ${global.__CLOSE_MSG}`);
  return global.__CLOSE_MSG || '';
}

(async () => {
  const m1 = await pasada(1);
  const m2 = await pasada(2);
  const m3 = await pasada(3);
  const rep = global.__report();
  console.log('\npáginas tras 3 pasadas:', rep.pages.join(', '));
  const fallo = [m1,m2,m3].some(m => m.indexOf('FALLÓ') === 0);
  const dup = [m1,m2,m3].some(m => m.indexOf('duplicate') !== -1);
  console.log('\n' + (fallo || dup ? '❌ sigue fallando' : '✅ idempotente: 3 pasadas seguidas sin error'));
  if (rep.errors.length) { console.log('\ninvariantes:'); [...new Set(rep.errors)].forEach(e=>console.log('  · '+e)); }
})();
