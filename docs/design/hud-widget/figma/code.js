/* ============================================================
   J Voice — HUD Builder  ·  v2
   Construye tokens, estilos, componentes con variantes y los
   tres estados del widget en el archivo Figma abierto.

   Ejecuta en local vía Plugins → Development.
   No consume cuota del MCP de Figma.

   v2 — idempotente: se puede ejecutar tantas veces como quieras.
        Reutiliza variables y estilos existentes en vez de
        duplicarlos, y reporta en qué paso falla si algo va mal.
   ============================================================ */

(async () => {

  /* ============ diagnóstico ============ */

  let STEP = 'inicio';
  const done = [];
  const warnings = [];

  // envuelve lo esencial: si falla, sabemos exactamente dónde
  async function step(name, fn) {
    STEP = name;
    const r = await fn();
    done.push(name);
    return r;
  }

  // envuelve lo prescindible: si falla, avisa pero no tumba el build
  function optional(name, fn) {
    try { return fn(); }
    catch (e) { warnings.push(`${name}: ${e.message}`); return null; }
  }

  /* ============ utilidades ============ */

  const rgb = (r, g, b) => ({ r: r / 255, g: g / 255, b: b / 255 });

  let FONT = { regular: null, medium: null, semibold: null, mono: null };

  async function resolveFonts() {
    const all = await figma.listAvailableFontsAsync();
    const has = (fam, sty) => all.some(f => f.fontName.family === fam && f.fontName.style === sty);

    if (has('SF Pro Text', 'Regular')) {
      FONT.regular  = { family: 'SF Pro Text', style: 'Regular' };
      FONT.medium   = { family: 'SF Pro Text', style: has('SF Pro Text', 'Medium') ? 'Medium' : 'Regular' };
      FONT.semibold = { family: 'SF Pro Text', style: has('SF Pro Text', 'Semibold') ? 'Semibold' : 'Bold' };
    } else if (has('Inter', 'Regular')) {
      FONT.regular  = { family: 'Inter', style: 'Regular' };
      FONT.medium   = { family: 'Inter', style: has('Inter', 'Medium') ? 'Medium' : 'Regular' };
      FONT.semibold = { family: 'Inter', style: has('Inter', 'Semi Bold') ? 'Semi Bold' : 'Bold' };
    } else {
      FONT.regular  = { family: 'Roboto', style: 'Regular' };
      FONT.medium   = { family: 'Roboto', style: 'Medium' };
      FONT.semibold = { family: 'Roboto', style: 'Bold' };
    }

    if (has('SF Mono', 'Regular'))          FONT.mono = { family: 'SF Mono', style: 'Regular' };
    else if (has('Roboto Mono', 'Regular')) FONT.mono = { family: 'Roboto Mono', style: 'Regular' };
    else                                    FONT.mono = FONT.regular;

    const uniq = [];
    [FONT.regular, FONT.medium, FONT.semibold, FONT.mono].forEach(f => {
      if (!uniq.some(u => u.family === f.family && u.style === f.style)) uniq.push(f);
    });
    for (const f of uniq) await figma.loadFontAsync(f);
    return `${FONT.regular.family} + ${FONT.mono.family}`;
  }

  function text(chars, opts) {
    opts = opts || {};
    const t = figma.createText();
    t.fontName = opts.font || FONT.regular;
    t.characters = chars;
    t.fontSize = opts.size || 13;
    if (opts.lineHeight) t.lineHeight = { unit: 'PIXELS', value: opts.lineHeight };
    if (opts.tracking !== undefined) t.letterSpacing = { unit: 'PERCENT', value: opts.tracking };
    if (opts.align) t.textAlignHorizontal = opts.align;
    if (opts.fill) t.fills = [opts.fill];
    if (opts.name) t.name = opts.name;
    return t;
  }

  // resize() RESETEA los modos de sizing → siempre resize primero, modos después.
  // Este orden es la causa del bug de layouts colapsados; no invertirlo.
  function sizing(f, w, h, hugW, hugH) {
    f.resize(Math.max(w, 0.01), Math.max(h, 0.01));
    const vertical = f.layoutMode === 'VERTICAL';
    f.primaryAxisSizingMode = (vertical ? hugH : hugW) ? 'AUTO' : 'FIXED';
    f.counterAxisSizingMode = (vertical ? hugW : hugH) ? 'AUTO' : 'FIXED';
    return f;
  }

  // append PRIMERO, posicionar DESPUÉS — al revés las coordenadas se reinterpretan
  function place(parent, node, x, y) {
    parent.appendChild(node);
    node.x = x;
    node.y = y;
    return node;
  }

  /* ============ P1 · TOKENS (idempotente) ============ */

  const V = {};
  let tokenStats = { creadas: 0, reutilizadas: 0 };

  async function buildTokens() {
    const collections = await figma.variables.getLocalVariableCollectionsAsync();
    const allVars = await figma.variables.getLocalVariablesAsync();

    function getCollection(name) {
      const found = collections.find(c => c.name === name);
      if (found) return found;
      const made = figma.variables.createVariableCollection(name);
      collections.push(made);
      return made;
    }

    // ← la corrección del bug: buscar antes de crear
    function getVar(name, collection, type) {
      const found = allVars.find(v => v.name === name && v.variableCollectionId === collection.id);
      if (found) { tokenStats.reutilizadas++; return found; }
      const made = figma.variables.createVariable(name, collection, type);
      allVars.push(made);
      tokenStats.creadas++;
      return made;
    }

    // — Primitives —
    const prim = getCollection('Primitives');
    const pMode = prim.modes[0].modeId;
    optional('renombrar modo Primitives', () => prim.renameMode(pMode, 'Value'));

    const primDefs = [
      ['ink/900',   [30, 27, 38],    '--jv-ink-900'],
      ['white',     [255, 255, 255], '--jv-white'],
      ['malva/500', [124, 106, 174], '--jv-malva-500'],
      ['malva/400', [154, 135, 206], '--jv-malva-400'],
      ['sky/500',   [110, 140, 196], '--jv-sky-500']
    ];
    for (const [name, c, syn] of primDefs) {
      const v = getVar(name, prim, 'COLOR');
      v.setValueForMode(pMode, rgb(c[0], c[1], c[2]));
      v.scopes = [];
      optional(`code syntax ${name}`, () => v.setVariableCodeSyntax('WEB', `var(${syn})`));
      V[name] = v;
    }

    // — HUD semántico —
    const hud = getCollection('HUD');
    const hMode = hud.modes[0].modeId;
    optional('renombrar modo HUD', () => hud.renameMode(hMode, 'Dark'));

    // El alpha va DENTRO de la variable. Si se enlaza una variable de color
    // a un paint, Figma usa el RGBA de la variable e IGNORA paint.opacity.
    // Por eso estos no son alias: un alias arrastraría alpha = 1.
    const semDefs = [
      ['surface/hud',      [30, 27, 38],   0.72,  ['FRAME_FILL', 'SHAPE_FILL'],   '--jv-surface-hud'],
      ['surface/veil',     [30, 27, 38],   0.50,  ['FRAME_FILL', 'SHAPE_FILL'],   '--jv-surface-veil'],
      ['surface/whisper',  [30, 27, 38],   0.30,  ['FRAME_FILL', 'SHAPE_FILL'],   '--jv-surface-whisper'],
      ['label/primary',    [255, 255, 255],0.95,  ['TEXT_FILL'],                  '--jv-label'],
      ['label/secondary',  [255, 255, 255],0.62,  ['TEXT_FILL'],                  '--jv-label-2'],
      ['label/tertiary',   [255, 255, 255],0.48,  ['TEXT_FILL'],                  '--jv-label-3'],
      ['separator',        [255, 255, 255],0.09,  ['STROKE_COLOR', 'SHAPE_FILL'], '--jv-sep'],
      ['fill/subtle',      [255, 255, 255],0.055, ['FRAME_FILL', 'SHAPE_FILL'],   '--jv-fill-subtle'],
      ['edge',             [255, 255, 255],0.10,  ['STROKE_COLOR'],               '--jv-edge'],
      ['accent/orb-start', [154, 135, 206],1,     ['FRAME_FILL', 'SHAPE_FILL'],   '--jv-orb-start'],
      ['accent/orb-end',   [110, 140, 196],1,     ['FRAME_FILL', 'SHAPE_FILL'],   '--jv-orb-end'],
      ['accent/active',    [154, 135, 206],1,     ['TEXT_FILL', 'SHAPE_FILL'],    '--jv-accent']
    ];
    for (const [name, c, a, scopes, syn] of semDefs) {
      const v = getVar(name, hud, 'COLOR');
      v.setValueForMode(hMode, { r: c[0]/255, g: c[1]/255, b: c[2]/255, a });
      v.scopes = scopes;
      optional(`code syntax ${name}`, () => v.setVariableCodeSyntax('WEB', `var(${syn})`));
      V[name] = v;
    }

    // — Layout —
    const lay = getCollection('Layout');
    const lMode = lay.modes[0].modeId;
    optional('renombrar modo Layout', () => lay.renameMode(lMode, 'Value'));

    const layDefs = [
      ['space/xs', 4, ['GAP'], '--jv-space-xs'],
      ['space/sm', 8, ['GAP'], '--jv-space-sm'],
      ['space/md', 12, ['GAP'], '--jv-space-md'],
      ['space/lg', 16, ['GAP'], '--jv-space-lg'],
      ['space/xl', 20, ['GAP'], '--jv-space-xl'],
      ['radius/sm', 6, ['CORNER_RADIUS'], '--jv-r-sm'],
      ['radius/md', 13, ['CORNER_RADIUS'], '--jv-r-md'],
      ['radius/lg', 22, ['CORNER_RADIUS'], '--jv-r-lg'],
      ['radius/full', 999, ['CORNER_RADIUS'], '--jv-r-full']
    ];
    for (const [name, val, scopes, syn] of layDefs) {
      const v = getVar(name, lay, 'FLOAT');
      v.setValueForMode(lMode, val);
      v.scopes = scopes;
      optional(`code syntax ${name}`, () => v.setVariableCodeSyntax('WEB', `var(${syn})`));
      V[name] = v;
    }

    return `${tokenStats.creadas} creadas, ${tokenStats.reutilizadas} reutilizadas`;
  }

  // El alpha vive en la variable, no aquí.
  function paint(varName) {
    const base = { type: 'SOLID', color: { r: 1, g: 1, b: 1 } };
    if (!V[varName]) return base;
    try { return figma.variables.setBoundVariableForPaint(base, 'color', V[varName]); }
    catch (e) { warnings.push(`bind ${varName}: ${e.message}`); return base; }
  }

  // color suelto con alpha, para casos que no merecen token
  function rawPaint(c, a) {
    return { type: 'SOLID', color: { r: c[0]/255, g: c[1]/255, b: c[2]/255 }, opacity: a };
  }

  const MALVA = [154, 135, 206], SKY = [110, 140, 196], WHITE = [255, 255, 255], INK = [30, 27, 38];

  // Borde especular: brillante arriba, se apaga hacia abajo.
  // Un stroke uniforme en todo el perímetro es lo que hacía que se viera duro.
  const EDGE_SPECULAR = {
    type: 'GRADIENT_LINEAR',
    gradientTransform: [[0, 1, 0], [-1, 0, 1]],
    gradientStops: [
      { position: 0,    color: { r: 1, g: 1, b: 1, a: 0.30 } },
      { position: 0.30, color: { r: 1, g: 1, b: 1, a: 0.07 } },
      { position: 1,    color: { r: 1, g: 1, b: 1, a: 0.02 } }
    ]
  };

  /* ============ estilos de efecto (idempotente) ============ */

  const SHADOWS = [
    { type: 'INNER_SHADOW', color: { r: 1, g: 1, b: 1, a: 0.18 }, offset: { x: 0, y: 1 }, radius: 0, spread: 0, visible: true, blendMode: 'NORMAL' },
    { type: 'DROP_SHADOW',  color: { r: 0, g: 0, b: 0, a: 0.30 }, offset: { x: 0, y: 1 }, radius: 3, spread: 0, visible: true, blendMode: 'NORMAL' },
    { type: 'DROP_SHADOW',  color: { r: 0, g: 0, b: 0, a: 0.55 }, offset: { x: 0, y: 24 }, radius: 60, spread: -12, visible: true, blendMode: 'NORMAL' }
  ];

  async function buildEffectStyles() {
    const styles = await figma.getLocalEffectStylesAsync();

    let glass = styles.find(s => s.name === 'HUD/Glass');
    if (!glass) glass = figma.createEffectStyle();
    glass.name = 'HUD/Glass';
    glass.effects = [{ type: 'BACKGROUND_BLUR', radius: 50, visible: true }].concat(SHADOWS);

    let elev = styles.find(s => s.name === 'HUD/Elevation');
    if (!elev) elev = figma.createEffectStyle();
    elev.name = 'HUD/Elevation';
    elev.effects = SHADOWS;

    return glass;
  }

  /* ============ superficie de vidrio ============ */

  async function glassSurface(name, width, radius, glassStyle) {
    const f = figma.createFrame();
    f.name = name;
    f.layoutMode = 'VERTICAL';
    f.cornerRadius = radius;
    f.clipsContent = true;
    f.fills = [paint('surface/hud')];
    f.strokes = [EDGE_SPECULAR];
    f.strokeWeight = 1;
    f.strokeAlign = 'INSIDE';
    sizing(f, width, 10, false, true);

    // el await faltaba en v1
    if (glassStyle) {
      try { await f.setEffectStyleIdAsync(glassStyle.id); }
      catch (e) {
        warnings.push(`estilo de vidrio en ${name}: ${e.message}`);
        f.effects = [{ type: 'BACKGROUND_BLUR', radius: 50, visible: true }].concat(SHADOWS);
      }
    }
    return f;
  }

  /* ============ ORBE ============ */

  const ORB_GRADIENT = {
    type: 'GRADIENT_LINEAR',
    gradientTransform: [[0.7, -0.7, 0.5], [0.7, 0.7, -0.1]],
    gradientStops: [
      { position: 0, color: { r: 154 / 255, g: 135 / 255, b: 206 / 255, a: 1 } },
      { position: 1, color: { r: 110 / 255, g: 140 / 255, b: 196 / 255, a: 1 } }
    ]
  };

  function buildOrbSet(page) {
    const specs = [
      ['Idle',      22, 0.20, 0.55],
      ['Listening', 26, 0.65, 1.00],
      ['Thinking',  26, 0.45, 0.85],
      ['Speaking',  28, 0.75, 1.00]
    ];
    const comps = specs.map(([state, size, glow, op]) => {
      const c = figma.createComponent();
      c.name = `State=${state}`;
      c.layoutMode = 'HORIZONTAL';
      c.primaryAxisAlignItems = 'CENTER';
      c.counterAxisAlignItems = 'CENTER';
      c.fills = [];
      page.appendChild(c);
      sizing(c, 32, 32, false, false);

      const o = figma.createEllipse();
      o.name = 'Orb';
      o.fills = [ORB_GRADIENT];
      o.opacity = op;
      o.effects = [{
        type: 'DROP_SHADOW',
        color: { r: 124 / 255, g: 106 / 255, b: 174 / 255, a: glow },
        offset: { x: 0, y: 0 }, radius: 14, spread: 0, visible: true, blendMode: 'NORMAL'
      }];
      c.appendChild(o);
      o.layoutSizingHorizontal = 'FIXED';
      o.layoutSizingVertical = 'FIXED';
      o.resize(size, size);
      return c;
    });

    const set = figma.combineAsVariants(comps, page);
    set.name = 'Orb';
    optional('descripción Orb', () => {
      set.description = 'Indicador de estado de J. Único elemento de marca dentro del HUD.';
    });
    set.layoutMode = 'HORIZONTAL';
    set.itemSpacing = 20;
    set.paddingTop = set.paddingBottom = set.paddingLeft = set.paddingRight = 20;
    set.primaryAxisSizingMode = 'AUTO';
    set.counterAxisSizingMode = 'AUTO';
    place(page, set, 0, 0);
    return set;
  }

  // instancia de una variante concreta, sin depender de defaultVariant
  function instance(set, propValue) {
    const variant = set.children.find(c => c.name.indexOf(propValue) !== -1) || set.children[0];
    return variant.createInstance();
  }

  /* ============ TURNOS ============ */

  function buildTurnSet(page) {
    const make = (speaker) => {
      const isUser = speaker === 'User';
      const c = figma.createComponent();
      c.name = `Speaker=${speaker}`;
      c.layoutMode = 'HORIZONTAL';
      c.primaryAxisAlignItems = isUser ? 'MAX' : 'MIN';
      c.fills = [];
      page.appendChild(c);
      sizing(c, 344, 10, false, true);

      const t = text(
        isUser
          ? 'Ok J, recuérdame llamar al médico a las 5 y busca vuelos a Lisboa para el finde.'
          : 'Hecho, te lo recuerdo a las 16:55. Estoy mirando los vuelos.',
        {
          name: 'Body', font: FONT.regular,
          size: isUser ? 13 : 14.5,
          lineHeight: isUser ? 19.5 : 22.5,
          align: isUser ? 'RIGHT' : 'LEFT',
          fill: paint(isUser ? 'label/secondary' : 'label/primary')
        }
      );
      c.appendChild(t);
      t.layoutSizingHorizontal = 'FIXED';
      t.resize(isUser ? 300 : 330, t.height);
      t.textAutoResize = 'HEIGHT';
      return c;
    };

    const set = figma.combineAsVariants([make('User'), make('J')], page);
    set.name = 'Turn';
    optional('descripción Turn', () => {
      set.description = 'Turno sin burbuja. Tu turno recede (derecha, secundario); el de J domina (izquierda, primario).';
    });

    set.layoutMode = 'VERTICAL';
    set.itemSpacing = 20;
    set.paddingTop = set.paddingBottom = set.paddingLeft = set.paddingRight = 20;
    set.primaryAxisSizingMode = 'AUTO';
    set.counterAxisSizingMode = 'AUTO';
    place(page, set, 0, 180);
    return set;
  }

  /* ============ TOOL CALL ============ */

  function buildToolCallSet(page) {
    const make = (state) => {
      const c = figma.createComponent();
      c.name = `State=${state}`;
      c.layoutMode = 'VERTICAL';
      c.itemSpacing = 7;
      c.paddingTop = c.paddingBottom = 10;
      c.paddingLeft = c.paddingRight = 12;
      c.cornerRadius = 13;
      c.fills = [paint('fill/subtle')];
      c.strokes = [paint('edge')];
      c.strokeWeight = 0.5;
      page.appendChild(c);
      sizing(c, 344, 10, false, true);

      const row = figma.createFrame();
      row.name = 'Row';
      row.layoutMode = 'HORIZONTAL';
      row.itemSpacing = 9;
      row.counterAxisAlignItems = 'CENTER';
      row.fills = [];
      c.appendChild(row);
      sizing(row, 320, 10, false, true);
      row.layoutSizingHorizontal = 'FILL';

      const glyph = figma.createFrame();
      glyph.name = 'Glyph';
      glyph.cornerRadius = 6;
      glyph.layoutMode = 'HORIZONTAL';
      glyph.primaryAxisAlignItems = 'CENTER';
      glyph.counterAxisAlignItems = 'CENTER';
      glyph.fills = [rawPaint(MALVA, 0.22)];
      row.appendChild(glyph);
      sizing(glyph, 19, 19, false, false);
      glyph.appendChild(text('✈', { size: 10, font: FONT.medium, fill: paint('accent/active') }));

      const tname = text('search_flights', {
        name: 'Tool Name', font: FONT.mono, size: 11.5,
        fill: paint('label/primary')
      });
      row.appendChild(tname);
      tname.layoutSizingHorizontal = 'FILL';

      const statusLabel = state === 'Running' ? 'en curso' : (state === 'Done' ? 'hecho' : 'error');
      row.appendChild(text(statusLabel, {
        name: 'Status', size: 10.5,
        fill: paint(state === 'Error' ? 'accent/active' : 'label/tertiary')
      }));

      const args = text('MAD → LIS · vie–dom', {
        name: 'Args', font: FONT.mono, size: 10.5, lineHeight: 17,
        fill: paint('label/tertiary')
      });
      c.appendChild(args);
      args.layoutSizingHorizontal = 'FILL';

      if (state === 'Running') {
        const track = figma.createFrame();
        track.name = 'Progress';
        track.cornerRadius = 2;
        track.fills = [paint('separator')];
        track.clipsContent = true;
        track.layoutMode = 'HORIZONTAL';
        c.appendChild(track);
        sizing(track, 320, 1.5, false, false);
        track.layoutSizingHorizontal = 'FILL';

        const bar = figma.createRectangle();
        bar.name = 'Bar';
        bar.cornerRadius = 2;
        bar.fills = [ORB_GRADIENT];
        track.appendChild(bar);
        bar.layoutSizingHorizontal = 'FIXED';
        bar.layoutSizingVertical = 'FILL';
        bar.resize(120, 1.5);
      }
      return c;
    };

    const set = figma.combineAsVariants([make('Running'), make('Done'), make('Error')], page);
    set.name = 'Widget / Tool Call';
    optional('descripción Tool Call', () => {
      set.description = 'Llamada a herramienta. Glifo monocromo + nombre en monoespaciada. Sin badges de color.';
    });
    set.layoutMode = 'VERTICAL';
    set.itemSpacing = 16;
    set.paddingTop = set.paddingBottom = set.paddingLeft = set.paddingRight = 20;
    set.primaryAxisSizingMode = 'AUTO';
    set.counterAxisSizingMode = 'AUTO';
    place(page, set, 480, 0);
    return set;
  }

  /* ============ TABLA ============ */

  function buildTable(page) {
    const c = figma.createComponent();
    c.name = 'Widget / Table';
    c.layoutMode = 'VERTICAL';
    c.itemSpacing = 0;
    c.fills = [];
    page.appendChild(c);
    sizing(c, 344, 10, false, true);
    optional('descripción Table', () => {
      c.description = 'Tabla de resultados. Solo hairlines, cifras tabulares a la derecha.';
    });

    const rows = [
      ['Vuelo', 'Salida', 'Precio', true, false],
      ['TP1234', 'vie 18:20', '89 €', false, true],
      ['IB5566', 'vie 20:05', '104 €', false, false],
      ['TP1236', 'dom 21:15', '96 €', false, false]
    ];

    rows.forEach(([a, b, d, isHead, isBest]) => {
      const r = figma.createFrame();
      r.name = isHead ? 'Header' : 'Row';
      r.layoutMode = 'HORIZONTAL';
      r.itemSpacing = 8;
      r.counterAxisAlignItems = 'CENTER';
      r.paddingTop = isHead ? 0 : 8;
      r.paddingBottom = isHead ? 7 : 8;
      r.fills = [];
      c.appendChild(r);
      sizing(r, 344, 10, false, true);
      r.layoutSizingHorizontal = 'FILL';

      if (!isHead) {
        optional('hairline de fila', () => {
          r.strokes = [paint('separator')];
          r.strokeTopWeight = 0.5;
          r.strokeBottomWeight = 0;
          r.strokeLeftWeight = 0;
          r.strokeRightWeight = 0;
        });
      }

      [a, b, d].forEach((val, ci) => {
        const t = text(isHead ? val.toUpperCase() : val, {
          font: isHead ? FONT.semibold : FONT.regular,
          size: isHead ? 9.5 : 12.5,
          tracking: isHead ? 7 : 0,
          align: ci === 0 ? 'LEFT' : 'RIGHT',
          fill: isHead
            ? paint('label/tertiary')
            : paint(isBest ? 'accent/active' : 'label/primary')
        });
        r.appendChild(t);
        if (ci === 0) {
          t.layoutSizingHorizontal = 'FILL';
        } else {
          t.layoutSizingHorizontal = 'FIXED';
          t.resize(ci === 1 ? 74 : 58, t.height);
        }
      });
    });

    place(page, c, 480, 380);
    return c;
  }

  /* ============ IMAGEN ============ */

  function buildImage(page) {
    const c = figma.createComponent();
    c.name = 'Widget / Image';
    c.layoutMode = 'VERTICAL';
    c.itemSpacing = 7;
    c.fills = [];
    page.appendChild(c);
    sizing(c, 344, 10, false, true);
    optional('descripción Image', () => {
      c.description = 'Adjunto visual. Imagen a sangre con radio 13 y caption suelto.';
    });

    const ph = figma.createFrame();
    ph.name = 'Image';
    ph.cornerRadius = 13;
    ph.layoutMode = 'HORIZONTAL';
    ph.primaryAxisAlignItems = 'CENTER';
    ph.counterAxisAlignItems = 'CENTER';
    ph.fills = [{
      type: 'GRADIENT_LINEAR',
      gradientTransform: [[0.9, 0.3, 0], [-0.3, 0.9, 0.05]],
      gradientStops: [
        { position: 0, color: { r: 154 / 255, g: 135 / 255, b: 206 / 255, a: 0.5 } },
        { position: 1, color: { r: 110 / 255, g: 140 / 255, b: 196 / 255, a: 0.4 } }
      ]
    }];
    c.appendChild(ph);
    sizing(ph, 344, 118, false, false);
    ph.appendChild(text('Lisboa · 3 opciones marcadas', { size: 11, fill: paint('label/secondary') }));
    ph.layoutSizingHorizontal = 'FILL';
    ph.layoutSizingVertical = 'FIXED';

    c.appendChild(text('Mapa generado', { name: 'Caption', size: 10.5, fill: paint('label/tertiary') }));

    place(page, c, 480, 620);
    return c;
  }

  /* ============ ESTADOS ============ */

  function iconButton(glyph, label) {
    const b = figma.createFrame();
    b.name = label;
    b.cornerRadius = 6;
    b.layoutMode = 'HORIZONTAL';
    b.primaryAxisAlignItems = 'CENTER';
    b.counterAxisAlignItems = 'CENTER';
    b.fills = [paint('fill/subtle')];
    b.appendChild(text(glyph, { size: 11, fill: paint('label/secondary') }));
    sizing(b, 22, 22, false, false);
    return b;
  }

  function waveform() {
    const w = figma.createFrame();
    w.name = 'Waveform';
    w.layoutMode = 'HORIZONTAL';
    w.itemSpacing = 3;
    w.counterAxisAlignItems = 'CENTER';
    w.fills = [];
    [7, 15, 10, 18, 8, 12].forEach((h, i) => {
      const bar = figma.createRectangle();
      bar.name = `Bar ${i + 1}`;
      bar.cornerRadius = 2;
      bar.fills = [rawPaint(WHITE, 0.5)];
      w.appendChild(bar);
      bar.resize(2.5, h);
    });
    sizing(w, 36, 20, true, false);
    return w;
  }

  function header(parent, orbSet, statusText, withButtons) {
    const h = figma.createFrame();
    h.name = 'Header';
    h.layoutMode = 'HORIZONTAL';
    h.itemSpacing = 10;
    h.counterAxisAlignItems = 'CENTER';
    h.paddingTop = 14; h.paddingBottom = 12;
    h.paddingLeft = 14; h.paddingRight = 14;
    h.fills = [];
    parent.appendChild(h);
    sizing(h, 340, 10, false, true);
    h.layoutSizingHorizontal = 'FILL';

    h.appendChild(instance(orbSet, 'Listening'));

    const meta = figma.createFrame();
    meta.name = 'Meta';
    meta.layoutMode = 'VERTICAL';
    meta.itemSpacing = 1;
    meta.fills = [];
    h.appendChild(meta);
    sizing(meta, 100, 10, true, true);
    meta.layoutSizingHorizontal = 'FILL';
    meta.appendChild(text('J', { font: FONT.semibold, size: 13, fill: paint('label/primary') }));
    if (statusText) meta.appendChild(text(statusText, { size: 11, fill: paint('label/tertiary') }));

    if (withButtons) {
      h.appendChild(iconButton('⌃', 'Collapse'));
      h.appendChild(iconButton('✕', 'Close'));
    }
    return h;
  }

  async function buildStates(page, orbSet, turnSet, toolSet, tableComp, imageComp, glassStyle) {
    // --- A · Pill ---
    const pill = await glassSurface('HUD / Compact', 230, 999, glassStyle);
    page.appendChild(pill);
    const prow = figma.createFrame();
    prow.name = 'Row';
    prow.layoutMode = 'HORIZONTAL';
    prow.itemSpacing = 11;
    prow.counterAxisAlignItems = 'CENTER';
    prow.paddingTop = prow.paddingBottom = 9;
    prow.paddingLeft = 11; prow.paddingRight = 16;
    prow.fills = [];
    pill.appendChild(prow);
    sizing(prow, 230, 10, false, true);
    prow.layoutSizingHorizontal = 'FILL';
    prow.appendChild(instance(orbSet, 'Listening'));
    prow.appendChild(waveform());
    const ptxt = text('Escuchando…', { size: 12.5, fill: paint('label/secondary') });
    prow.appendChild(ptxt);
    ptxt.layoutSizingHorizontal = 'FILL';

    // --- B · Respuesta efímera ---
    const glance = await glassSurface('HUD / Glance', 340, 22, glassStyle);
    page.appendChild(glance);
    header(glance, orbSet, null, false);
    const gBody = figma.createFrame();
    gBody.name = 'Body';
    gBody.layoutMode = 'VERTICAL';
    gBody.paddingLeft = gBody.paddingRight = 18;
    gBody.paddingBottom = 18;
    gBody.fills = [];
    glance.appendChild(gBody);
    sizing(gBody, 340, 10, false, true);
    gBody.layoutSizingHorizontal = 'FILL';
    const gTurn = instance(turnSet, 'J');
    gBody.appendChild(gTurn);
    gTurn.layoutSizingHorizontal = 'FILL';

    // --- C · Panel completo ---
    const panel = await glassSurface('HUD / Panel', 380, 22, glassStyle);
    page.appendChild(panel);
    header(panel, orbSet, 'Buscando vuelos', true);

    const convo = figma.createFrame();
    convo.name = 'Conversation';
    convo.layoutMode = 'VERTICAL';
    convo.itemSpacing = 20;
    convo.paddingLeft = convo.paddingRight = 18;
    convo.paddingTop = 4; convo.paddingBottom = 16;
    convo.fills = [];
    panel.appendChild(convo);
    sizing(convo, 380, 10, false, true);
    convo.layoutSizingHorizontal = 'FILL';

    [
      instance(turnSet, 'User'),
      instance(turnSet, 'J'),
      instance(toolSet, 'Running'),
      tableComp.createInstance(),
      imageComp.createInstance()
    ].forEach(n => {
      convo.appendChild(n);
      n.layoutSizingHorizontal = 'FILL';
    });

    const div = figma.createRectangle();
    div.name = 'Separator';
    div.fills = [paint('separator')];
    panel.appendChild(div);
    div.resize(380, 0.5);
    div.layoutSizingHorizontal = 'FILL';

    const foot = figma.createFrame();
    foot.name = 'Footer';
    foot.layoutMode = 'HORIZONTAL';
    foot.itemSpacing = 8;
    foot.counterAxisAlignItems = 'CENTER';
    foot.paddingTop = foot.paddingBottom = 10;
    foot.paddingLeft = foot.paddingRight = 14;
    foot.fills = [];
    panel.appendChild(foot);
    sizing(foot, 380, 10, false, true);
    foot.layoutSizingHorizontal = 'FILL';

    const dot = figma.createEllipse();
    dot.fills = [paint('accent/active')];
    foot.appendChild(dot);
    dot.resize(5, 5);
    const lst = text('Escuchando…', { size: 11, fill: paint('label/secondary') });
    foot.appendChild(lst);
    lst.layoutSizingHorizontal = 'FILL';
    foot.appendChild(text('⌘⇧J', { font: FONT.mono, size: 10.5, fill: paint('label/tertiary') }));

    return { pill, glance, panel };
  }

  /* ============ PROTOTIPO INTERACTIVO ============ */

  // Tres pantallas con el mismo fondo. El HUD se llama igual en las tres,
  // así Smart Animate lo interpola en vez de hacer un corte.
  async function buildPrototype(page, orbSet, turnSet, toolSet, tableC, imageC, glass) {
    const s = await buildStates(page, orbSet, turnSet, toolSet, tableC, imageC, glass);

    const screens = [
      { node: s.pill,   label: '1 · Escuchando',        w: 230 },
      { node: s.glance, label: '2 · Respuesta efímera', w: 340 },
      { node: s.panel,  label: '3 · Panel completo',    w: 380 }
    ];

    const frames = screens.map((sc, i) => {
      const f = backdrop(page, sc.label, i * 1520, 'wall');
      f.appendChild(sc.node);
      sc.node.name = 'HUD';                    // nombre común → Smart Animate
      sc.node.x = 1440 - sc.w - 14;
      sc.node.y = 38;
      return f;
    });

    // easing del PLAN §5: cubic-bezier(.32,.72,0,1), 0.42 s
    const ease = { type: 'CUSTOM_CUBIC_BEZIER',
                   easingFunctionCubicBezier: { x1: 0.32, y1: 0.72, x2: 0, y2: 1 } };

    for (let i = 0; i < frames.length; i++) {
      const next = frames[(i + 1) % frames.length];
      const backwards = i === frames.length - 1;
      try {
        await frames[i].setReactionsAsync([{
          trigger: { type: 'ON_CLICK' },
          actions: [{
            type: 'NODE',
            destinationId: next.id,
            navigation: 'NAVIGATE',
            transition: { type: 'SMART_ANIMATE', easing: ease,
                          duration: backwards ? 0.24 : 0.42 },   // se va rápido, llega con calma
            preserveScrollPosition: false
          }]
        }]);
      } catch (e) {
        warnings.push(`prototipo ${i + 1}: ${e.message}`);
      }
    }

    optional('flow starting point', () => {
      page.flowStartingPoints = [{ nodeId: frames[0].id, name: 'J Voice · estados' }];
    });

    return frames;
  }

  /* ============================================================
     CONCEPTOS — exploración del estado de escucha
     De más etéreo a más definido. Ninguno es definitivo.
     ============================================================ */

  // Halo sin bordes: un óvalo difuminado que se apaga hacia fuera.
  // Da legibilidad al texto sin dibujar ningún contorno.
  function halo(parent, w, h, alpha, blur) {
    const g = figma.createEllipse();
    g.name = 'Halo';
    g.fills = [{
      type: 'GRADIENT_RADIAL',
      gradientTransform: [[1, 0, 0], [0, 1, 0]],
      gradientStops: [
        { position: 0,    color: { r: INK[0]/255, g: INK[1]/255, b: INK[2]/255, a: alpha } },
        { position: 0.55, color: { r: INK[0]/255, g: INK[1]/255, b: INK[2]/255, a: alpha * 0.7 } },
        { position: 1,    color: { r: INK[0]/255, g: INK[1]/255, b: INK[2]/255, a: 0 } }
      ]
    }];
    g.effects = [{ type: 'LAYER_BLUR', radius: blur, visible: true }];
    parent.appendChild(g);
    g.resize(w, h);
    return g;
  }

  function orbRaw(parent, size, glow) {
    const o = figma.createEllipse();
    o.name = 'Orb';
    o.fills = [ORB_GRADIENT];
    o.effects = [{ type: 'DROP_SHADOW', color: { r: 124/255, g: 106/255, b: 174/255, a: glow },
                   offset: { x: 0, y: 0 }, radius: 18, spread: 2, visible: true, blendMode: 'NORMAL' }];
    parent.appendChild(o);
    o.resize(size, size);
    return o;
  }

  // texto suelto sobre la pantalla, con sombra propia para legibilidad
  function floatingText(parent, chars, opts) {
    const t = text(chars, opts);
    t.effects = [{ type: 'DROP_SHADOW', color: { r: 0, g: 0, b: 0, a: 0.55 },
                   offset: { x: 0, y: 1 }, radius: 6, spread: 0, visible: true, blendMode: 'NORMAL' }];
    parent.appendChild(t);
    return t;
  }

  function conceptLabel(page, x, y, titulo, desc) {
    const t = text(titulo, { font: FONT.semibold, size: 15, fill: rawPaint(WHITE, 0.9) });
    place(page, t, x, y);
    const d = text(desc, { size: 11.5, lineHeight: 17, fill: rawPaint(WHITE, 0.45) });
    place(page, d, x, y + 22);
    d.resize(360, 34);
    d.textAutoResize = 'HEIGHT';
  }

  async function buildConcepts(page) {
    const W = 720, H = 420;
    const made = [];

    function stage(name, x, y) {
      const f = backdrop(page, name, x, 0, 'wall');
      f.resize(W, H);
      f.y = y;
      // recolocar la barra de menú al nuevo ancho
      const mb = f.children.find(c => c.name === 'Menu Bar');
      if (mb) { sizing(mb, W, 26, false, false); mb.x = 0; mb.y = 0; }
      made.push(f);
      return f;
    }

    /* ---- A · Aliento: sin contenedor, solo halo + texto ---- */
    const a = stage('A · Aliento', 0, 0);
    halo(a, 420, 190, 0.62, 34).x = W - 430;
    a.children[a.children.length - 1].y = 30;
    const aOrb = orbRaw(a, 20, 0.7); aOrb.x = W - 400; aOrb.y = 62;
    const aTxt = floatingText(a, 'recuérdame llamar al médico', {
      size: 15, lineHeight: 22, align: 'RIGHT', fill: rawPaint(WHITE, 0.96) });
    aTxt.resize(300, 22); aTxt.textAutoResize = 'HEIGHT';
    aTxt.x = W - 320; aTxt.y = 58;
    const aHint = floatingText(a, 'escuchando', {
      size: 10.5, tracking: 8, align: 'RIGHT', fill: rawPaint(WHITE, 0.4) });
    aHint.resize(300, 14); aHint.x = W - 320; aHint.y = 92;

    /* ---- B · Bruma: velo difuso, borde inexistente ---- */
    const b = stage('B · Bruma', W + 80, 0);
    const bVeil = figma.createFrame();
    bVeil.name = 'Veil';
    bVeil.cornerRadius = 26;
    bVeil.fills = [paint('surface/whisper')];
    bVeil.effects = [
      { type: 'BACKGROUND_BLUR', radius: 60, visible: true },
      { type: 'LAYER_BLUR', radius: 0, visible: false }
    ];
    bVeil.layoutMode = 'HORIZONTAL';
    bVeil.itemSpacing = 12;
    bVeil.counterAxisAlignItems = 'CENTER';
    bVeil.paddingTop = bVeil.paddingBottom = 14;
    bVeil.paddingLeft = 16; bVeil.paddingRight = 20;
    b.appendChild(bVeil);
    sizing(bVeil, 320, 10, false, true);
    bVeil.x = W - 336; bVeil.y = 44;
    orbRaw(bVeil, 18, 0.6);
    const bTxt = text('busca vuelos a Lisboa…', { size: 14, fill: paint('label/primary') });
    bVeil.appendChild(bTxt);
    bTxt.layoutSizingHorizontal = 'FILL';

    /* ---- C · Cristal: vidrio real, borde solo especular arriba ---- */
    const c = stage('C · Cristal', 0, H + 110);
    const cGlass = figma.createFrame();
    cGlass.name = 'Glass';
    cGlass.cornerRadius = 22;
    cGlass.fills = [paint('surface/veil')];
    cGlass.strokes = [EDGE_SPECULAR];
    cGlass.strokeWeight = 1;
    cGlass.strokeAlign = 'INSIDE';
    cGlass.effects = [
      { type: 'BACKGROUND_BLUR', radius: 50, visible: true },
      { type: 'DROP_SHADOW', color: { r: 0, g: 0, b: 0, a: 0.42 },
        offset: { x: 0, y: 18 }, radius: 44, spread: -14, visible: true, blendMode: 'NORMAL' }
    ];
    cGlass.layoutMode = 'HORIZONTAL';
    cGlass.itemSpacing = 12;
    cGlass.counterAxisAlignItems = 'CENTER';
    cGlass.paddingTop = cGlass.paddingBottom = 13;
    cGlass.paddingLeft = 15; cGlass.paddingRight = 20;
    c.appendChild(cGlass);
    sizing(cGlass, 300, 10, false, true);
    cGlass.x = W - 316; cGlass.y = 44;
    orbRaw(cGlass, 18, 0.55);
    cGlass.appendChild(waveform());
    const cTxt = text('escuchando…', { size: 13, fill: paint('label/secondary') });
    cGlass.appendChild(cTxt);
    cTxt.layoutSizingHorizontal = 'FILL';

    /* ---- D · Estela: la transcripción cae como líneas sueltas ---- */
    const d = stage('D · Estela · transcripción en vivo', W + 80, H + 110);
    halo(d, 460, 230, 0.5, 40).x = W - 470;
    d.children[d.children.length - 1].y = 24;
    const dOrb = orbRaw(d, 16, 0.65); dOrb.x = W - 380; dOrb.y = 54;

    // cada línea más vieja se apaga y encoge: da sensación de flujo
    const lineas = [
      ['ok J', 11, 0.22],
      ['recuérdame llamar', 13, 0.42],
      ['al médico a las cinco', 15, 0.72],
      ['y busca vuelos a Lisboa', 17, 0.98]
    ];
    let yy = 50;
    lineas.forEach(([txt, size, alpha]) => {
      const l = floatingText(d, txt, {
        size, lineHeight: size + 9, align: 'RIGHT', fill: rawPaint(WHITE, alpha) });
      l.resize(300, size + 9);
      l.textAutoResize = 'HEIGHT';
      l.x = W - 320; l.y = yy;
      yy += size + 13;
    });

    conceptLabel(page, 0, H + 24,
      'A · Aliento — sin contenedor',
      'Solo un halo difuminado detrás del texto. Cero bordes, cero superficie. Contraste medido: 4.5:1 sobre blanco — justo en el límite, y solo porque el halo va al 62 % en el centro. Fuera del halo cae a 1.0:1.');
    conceptLabel(page, W + 80, H + 24,
      'B · Bruma — velo al 30 %',
      'Blur alto, sin stroke, el fondo se ve casi entero. PERO: 1.9:1 sobre un documento blanco — ilegible. Solo viable si la opacidad se adapta al fondo.');
    conceptLabel(page, 0, 2 * H + 134,
      'C · Cristal — vidrio con especular',
      'Vidrio al 50 % con borde brillante solo arriba, apagándose hacia abajo. 3.1:1 sobre blanco: aceptable para texto grande, justo para texto pequeño. El material más Apple de los cuatro.');
    conceptLabel(page, W + 80, 2 * H + 134,
      'D · Estela — transcripción en vivo',
      'Las palabras entran abajo; las anteriores se apagan y encogen al subir. Sin contenedor: el texto es la interfaz. Combinable con cualquiera de los otros tres.');

    // La conclusión incómoda, escrita en la propia página
    const nota = text(
      'La tensión de fondo\n\n' +
      'Cuanto más etéreo, menos legible. Medido sobre documento blanco:\n' +
      '   hud 72 %  → 6.2:1   ✓ cómodo\n' +
      '   veil 50 % → 3.1:1   ~ solo texto grande\n' +
      '   whisper 30 % → 1.9:1   ✗ ilegible\n' +
      '   sin fondo → 1.0:1   ✗ invisible\n\n' +
      'Sobre wallpaper oscuro los cuatro pasan de sobra. El problema aparece\n' +
      'solo sobre fondos claros — y ahí es donde el usuario trabaja.\n\n' +
      'Salida probable: opacidad adaptativa. Muestrear la luminancia de lo que\n' +
      'hay detrás y mover la superficie entre 30 % y 72 % según haga falta.\n' +
      'macOS ya hace algo así con sus materiales.',
      { font: FONT.mono, size: 12, lineHeight: 19, fill: rawPaint(WHITE, 0.62) });
    place(page, nota, 0, 3 * 420 + 250);
    nota.resize(680, 300);
    nota.textAutoResize = 'HEIGHT';

    return made;
  }

  /* ============ FONDOS ============ */

  function backdrop(page, name, x, kind) {
    const f = figma.createFrame();
    f.name = name;
    f.cornerRadius = 12;
    f.clipsContent = true;
    page.appendChild(f);
    f.resize(1440, 900);
    f.x = x; f.y = 0;

    if (kind === 'wall') {
      f.fills = [{
        type: 'GRADIENT_LINEAR',
        gradientTransform: [[0.8, 0.6, 0], [-0.6, 0.8, 0.2]],
        gradientStops: [
          { position: 0,    color: { r: 26 / 255,  g: 19 / 255, b: 56 / 255, a: 1 } },
          { position: 0.45, color: { r: 36 / 255,  g: 58 / 255, b: 99 / 255, a: 1 } },
          { position: 1,    color: { r: 122 / 255, g: 53 / 255, b: 96 / 255, a: 1 } }
        ]
      }];
    } else if (kind === 'doc') {
      f.fills = [{ type: 'SOLID', color: rgb(255, 255, 255) }];
      place(f, text('Informe trimestral · resultados Q3', {
        font: FONT.semibold, size: 30, fill: { type: 'SOLID', color: rgb(17, 17, 17) }
      }), 90, 80);

      const body = text(
        'El crecimiento sostenido durante el tercer trimestre confirma la tendencia observada en los dos periodos anteriores, con una mejora notable en los márgenes operativos y una reducción del coste de adquisición.',
        { size: 15, lineHeight: 26, fill: { type: 'SOLID', color: rgb(58, 58, 58) } }
      );
      place(f, body, 90, 140);
      body.resize(720, 80);
      body.textAutoResize = 'HEIGHT';

      [45, 70, 55, 88, 62, 100, 78].forEach((h, i) => {
        const bar = figma.createRectangle();
        bar.fills = [{ type: 'SOLID', color: rgb(74, 144, 217) }];
        f.appendChild(bar);
        bar.resize(70, h * 1.5);
        bar.x = 90 + i * 82;
        bar.y = 480 - h * 1.5;
        bar.topLeftRadius = bar.topRightRadius = 5;
      });
    } else {
      f.fills = [{ type: 'SOLID', color: rgb(22, 22, 28) }];
      [
        '# jota-voice · overlay window',
        'import AppKit',
        '',
        'final class HUDPanel: NSPanel {',
        '    override init(contentRect: NSRect) {',
        '        level = .floating',
        '        isOpaque = false',
        '        backgroundColor = .clear',
        '        collectionBehavior = [.canJoinAllSpaces]',
        '    }',
        '}',
        '',
        '// material: .hudWindow + vibrancy',
        'let fx = NSVisualEffectView()',
        'fx.material = .hudWindow'
      ].forEach((ln, i) => {
        place(f, text(ln || ' ', {
          font: FONT.mono, size: 13.5, fill: { type: 'SOLID', color: rgb(140, 140, 160) }
        }), 70, 60 + i * 28);
      });
    }

    // barra de menú
    const mb = figma.createFrame();
    mb.name = 'Menu Bar';
    mb.fills = [{ type: 'SOLID', color: rgb(0, 0, 0), opacity: 0.28 }];
    mb.layoutMode = 'HORIZONTAL';
    mb.primaryAxisAlignItems = 'MAX';
    mb.counterAxisAlignItems = 'CENTER';
    mb.itemSpacing = 20;
    mb.paddingLeft = mb.paddingRight = 16;
    f.appendChild(mb);
    sizing(mb, 1440, 26, false, false);
    mb.x = 0; mb.y = 0;

    const mbOrb = figma.createEllipse();
    mbOrb.fills = [ORB_GRADIENT];
    mb.appendChild(mbOrb);
    mbOrb.resize(14, 14);
    mb.appendChild(text('Dom 2 ago  14:32', { size: 12.5, fill: { type: 'SOLID', color: rgb(255, 255, 255) } }));

    return f;
  }

  /* ============ ORQUESTACIÓN ============ */

  try {
    const fonts = await step('cargar tipografías', resolveFonts);
    const tokens = await step('crear tokens', buildTokens);
    const glass = await step('crear estilos de efecto', buildEffectStyles);

    // páginas limpias — nunca borrar la página activa ni la última que quede
    await step('preparar páginas', async () => {
      const safe = figma.root.children.find(p => p.name.indexOf('JV · ') !== 0);
      if (safe) await figma.setCurrentPageAsync(safe);
      for (const name of ['JV · Components', 'JV · States', 'JV · Prototype', 'JV · Conceptos']) {
        const p = figma.root.children.find(pg => pg.name === name);
        if (p && figma.root.children.length > 1 && p.id !== figma.currentPage.id) p.remove();
      }
    });

    const compPage = figma.createPage();
    compPage.name = 'JV · Components';
    await figma.setCurrentPageAsync(compPage);

    const orbSet  = await step('componente Orb', async () => buildOrbSet(compPage));
    const turnSet = await step('componente Turn', async () => buildTurnSet(compPage));
    const toolSet = await step('componente Tool Call', async () => buildToolCallSet(compPage));
    const tableC  = await step('componente Table', async () => buildTable(compPage));
    const imageC  = await step('componente Image', async () => buildImage(compPage));

    const statePage = figma.createPage();
    statePage.name = 'JV · States';
    await figma.setCurrentPageAsync(statePage);

    const beds = await step('fondos de contexto', async () => ({
      wall: backdrop(statePage, 'Contexto · Wallpaper', 0, 'wall'),
      doc:  backdrop(statePage, 'Contexto · Documento blanco', 1520, 'doc'),
      code: backdrop(statePage, 'Contexto · Editor oscuro', 3040, 'code')
    }));

    const s = await step('ensamblar estados', async () =>
      buildStates(statePage, orbSet, turnSet, toolSet, tableC, imageC, glass));

    await step('colocar estados sobre los fondos', async () => {
      beds.wall.appendChild(s.panel);  s.panel.x  = 1440 - 380 - 14; s.panel.y  = 38;
      beds.doc.appendChild(s.glance);  s.glance.x = 1440 - 340 - 14; s.glance.y = 38;
      beds.code.appendChild(s.pill);   s.pill.x   = 1440 - 230 - 14; s.pill.y   = 38;
    });

    const conceptPage = figma.createPage();
    conceptPage.name = 'JV · Conceptos';
    await figma.setCurrentPageAsync(conceptPage);
    await step('conceptos de escucha', async () => buildConcepts(conceptPage));

    const protoPage = figma.createPage();
    protoPage.name = 'JV · Prototype';
    await figma.setCurrentPageAsync(protoPage);
    const protoFrames = await step('prototipo interactivo', async () =>
      buildPrototype(protoPage, orbSet, turnSet, toolSet, tableC, imageC, glass));

    await figma.setCurrentPageAsync(statePage);
    figma.currentPage.selection = [beds.wall];
    figma.viewport.scrollAndZoomIntoView([beds.wall, beds.doc, beds.code]);

    let msg = `Listo · tokens: ${tokens} · fuentes: ${fonts} · 5 componentes, 3 estados, prototipo de ${protoFrames.length} pantallas, 4 conceptos`;
    if (warnings.length) msg += ` · ${warnings.length} aviso(s): ${warnings.slice(0, 2).join(' | ')}`;
    figma.closePlugin(msg);

  } catch (e) {
    figma.closePlugin(`FALLÓ en "${STEP}" → ${e.message}  ·  completado: ${done.join(', ') || 'nada'}`);
  }
})();
