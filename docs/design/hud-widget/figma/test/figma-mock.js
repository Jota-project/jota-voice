// Mock de la Figma Plugin API — valida invariantes reales, no solo sintaxis
const errors = [], created = [];
let idc = 0;

const AUTOLAYOUT = new Set(['FRAME','COMPONENT','COMPONENT_SET','INSTANCE']);

function node(type, extra = {}) {
  const n = {
    id: 'n' + (++idc), type, name: '', children: [], parent: null,
    width: 100, height: 100, x: 0, y: 0,
    fills: [], strokes: [], effects: [], opacity: 1,
    layoutMode: 'NONE', _appended: false,
    resize(w, h) {
      if (typeof w !== 'number' || typeof h !== 'number' || isNaN(w) || isNaN(h))
        errors.push(`${this.name||this.type}: resize(${w},${h}) inválido`);
      if (w <= 0 || h <= 0)
        errors.push(`${this.name||this.type}: resize(${w},${h}) con dimensión <= 0`);
      // Figma resetea los modos de sizing al hacer resize
      if (this._sizingModesSet)
        errors.push(`${this.name||this.type}: resize() DESPUÉS de fijar sizing modes — los resetea a FIXED y el hug se pierde`);
      this._primaryAxisSizingMode = 'FIXED';
      this._counterAxisSizingMode = 'FIXED';
      this.width = w; this.height = h;
    },
    appendChild(c) {
      if (!c) { errors.push(`${this.name}: appendChild(undefined)`); return; }
      if (c.parent) c.parent.children = c.parent.children.filter(x => x !== c);
      c.parent = this; c._appended = true; this.children.push(c);
    },
    findOne(fn) { 
      for (const c of this.children) { if (fn(c)) return c; const r = c.findOne && c.findOne(fn); if (r) return r; }
      return null;
    },
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(x => x !== this); },
    createInstance() { 
      if (this.type !== 'COMPONENT') errors.push(`createInstance sobre ${this.type}, no COMPONENT`);
      return node('INSTANCE', { name: this.name + ' (inst)' });
    },
    setEffectStyleIdAsync: async () => {},
    setReactionsAsync: async function (rs) {
      if (!Array.isArray(rs)) throw new Error('setReactionsAsync sin array');
      rs.forEach(r => {
        if (!r.trigger || !r.trigger.type) errors.push('reacción sin trigger');
        (r.actions || []).forEach(a => {
          if (a.type === 'NODE' && !a.destinationId) errors.push('acción NODE sin destinationId');
          if (a.transition && ['SMART_ANIMATE','DISSOLVE','MOVE_IN','PUSH','SLIDE_IN'].indexOf(a.transition.type) === -1)
            errors.push('transición inválida: ' + a.transition.type);
        });
      });
      this._reactions = rs;
    },
    addComponentProperty(name, type, def) {
      if (this.type !== 'COMPONENT' && this.type !== 'COMPONENT_SET')
        throw new Error(`addComponentProperty sobre ${this.type}`);
      return name + '#1:1';
    },
    get componentPropertyReferences() { return this._cpr; },
    set componentPropertyReferences(v) { this._cpr = v; }
  };
  ['primaryAxisSizingMode','counterAxisSizingMode'].forEach(prop => {
    Object.defineProperty(n, prop, {
      set(v) {
        if (this.layoutMode === 'NONE')
          errors.push(`${this.name||this.type}: ${prop} en nodo sin auto-layout`);
        if (v !== 'FIXED' && v !== 'AUTO')
          errors.push(`${this.name||this.type}: ${prop}='${v}' inválido (solo FIXED|AUTO)`);
        if (v === 'AUTO') this._sizingModesSet = true;
        this['_' + prop] = v;
      },
      get() { return this['_' + prop]; }
    });
  });

  // invariante clave: FILL/HUG requieren padre auto-layout
  ['layoutSizingHorizontal','layoutSizingVertical'].forEach(prop => {
    Object.defineProperty(n, prop, {
      set(v) {
        if (v === 'FILL') {
          if (!this.parent) errors.push(`${this.name||this.type}: ${prop}='FILL' sin padre`);
          else if (!AUTOLAYOUT.has(this.parent.type) || this.parent.layoutMode === 'NONE')
            errors.push(`${this.name||this.type}: ${prop}='FILL' pero el padre "${this.parent.name}" no es auto-layout (layoutMode=${this.parent.layoutMode})`);
        }
        if (v === 'HUG' && this.type !== 'TEXT' && this.layoutMode === 'NONE')
          errors.push(`${this.name||this.type}: ${prop}='HUG' en nodo sin auto-layout`);
        this['_' + prop] = v;
      },
      get() { return this['_' + prop]; }
    });
  });
  Object.assign(n, extra);
  created.push(n);
  return n;
}

const vars = [];
const collections = [];
const effectStyles = [];
const pages = [node('PAGE', { name: 'Page 1' })];

global.figma = {
  root: { get children() { return pages; } },
  currentPage: pages[0],
  createFrame: () => node('FRAME'),
  createComponent: () => node('COMPONENT'),
  createText: () => { const t = node('TEXT'); t.characters=''; t.fontName={family:'Inter',style:'Regular'}; t.fontSize=12; return t; },
  createEllipse: () => node('ELLIPSE'),
  createRectangle: () => node('RECTANGLE'),
  createPage: () => {
    const p = node('PAGE');
    p.remove = function () {
      const i = pages.indexOf(this);
      if (i === -1) { errors.push('remove() de página no registrada'); return; }
      if (pages.length <= 1) { errors.push('intento de borrar la última página'); return; }
      if (figma.currentPage === this) { errors.push('intento de borrar la página ACTIVA'); return; }
      pages.splice(i, 1);
    };
    pages.push(p); return p;
  },
  setCurrentPageAsync: async (p) => { if (!p) throw new Error('setCurrentPageAsync(undefined)'); figma.currentPage = p; },
  combineAsVariants: (comps, parent) => {
    if (!Array.isArray(comps) || !comps.length) throw new Error('combineAsVariants sin componentes');
    comps.forEach(c => { if (c.type !== 'COMPONENT') errors.push('combineAsVariants con no-COMPONENT'); });
    const set = node('COMPONENT_SET');
    comps.forEach(c => set.appendChild(c));
    parent.appendChild(set);
    return set;
  },
  loadFontAsync: async () => {},
  listAvailableFontsAsync: async () => ([
    { fontName: { family: 'Inter', style: 'Regular' } },
    { fontName: { family: 'Inter', style: 'Medium' } },
    { fontName: { family: 'Inter', style: 'Semi Bold' } },
    { fontName: { family: 'Roboto Mono', style: 'Regular' } }
  ]),
  getLocalEffectStylesAsync: async () => effectStyles,
  createEffectStyle: () => { const s = { id: 's'+(++idc), name: '', effects: [] }; effectStyles.push(s); return s; },
  variables: {
    getLocalVariableCollectionsAsync: async () => collections,
    getLocalVariablesAsync: async () => vars,
    createVariableCollection: (name) => {
      if (collections.find(c => c.name === name)) throw new Error('duplicate collection');
      const c = { id: 'c'+(++idc), name, modes: [{ modeId: 'm1', name: 'Mode 1' }], variableIds: [],
                  renameMode(){}, };
      collections.push(c); return c;
    },
    createVariable: (name, coll, type) => {
      if (vars.find(v => v.name === name && v.variableCollectionId === coll.id))
        throw new Error('in createVariable: duplicate variable name');   // ← el bug reportado
      const v = { id:'v'+(++idc), name, variableCollectionId: coll.id, resolvedType: type,
                  scopes: [], setValueForMode(){}, setVariableCodeSyntax(){} };
      vars.push(v); return v;
    },
    setBoundVariableForPaint: (p, f, v) => {
      if (!v) throw new Error('setBoundVariableForPaint con variable undefined');
      return Object.assign({}, p, { boundVariables: { color: { id: v.id } } });
    }
  },
  viewport: { scrollAndZoomIntoView(){} },
  closePlugin: (msg) => { global.__CLOSE_MSG = msg; }
};

global.__report = () => ({ errors, nodes: created.length, vars: vars.length, pages: pages.map(p=>p.name) });
