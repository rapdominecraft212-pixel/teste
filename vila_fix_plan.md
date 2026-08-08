# Plano de Correção - Mapa Vila (DARK COVE v0.12) - APLICADO

## 1. Problemas Identificados e Corrigidos

### 1.1 Paredes Viradas Errado ✅ CORrigido
**Sintoma:** Algumas paredes das casas (izbas) estavam com orientação/rotação inconsistente.

**Correção Aplicada:**
- Removida lógica complexa `odd` que causava rotação dupla
- Simplificada a função `seg()` para usar rotação consistente: `wallRot = rad + (localRy ? Math.PI/2 : 0)`
- Colisores das paredes agora usam cálculo direto baseado na orientação local

**Arquivo:** `/workspace/tarkov.html` linhas 1116-1146

### 1.2 Z-Fighting (Flickering de Superfícies Sobrepostas) ✅ CORrigido
**Sintoma:** Flickering visual quando superfícies muito próximas se sobrepõem.

**Correções Aplicadas:**

1. **Shadow bias ajustado** (linha 1062):
   - `bias`: -0.0006 → **-0.001**
   - Adicionado `normalBias: 0.02`

2. **PolygonOffset em todos os materiais** (linhas 1088-1104):
   - Função `SM()` agora inclui `polygonOffset:true, polygonOffsetFactor:1, polygonOffsetUnits:1`
   - Materiais especiais (grass, leaf, etc.) configurados individualmente
   - Grass recebe factor=2 para maior separação

3. **Alturas de superfícies separadas** (linhas 1179-1196):
   - Chão (gnd): y = 0 (mantido)
   - Estrada (road): y = 0.02 → **0.05**
   - Path: y = 0.02 → **0.05**
   - Splats: y = 0.03 → **0.06**

### 1.3 Objetos Reposicionados ✅ CORrigido

| Objeto | Posição Antiga | Nova Posição | Motivo |
|--------|---------------|--------------|--------|
| Well (poço) | (10, 3.1) | **(10, 5)** | Afastado da estrada |
| Barrel 2 | (-13.8, 7.4) | **(-12.5, 8.2)** | Separado do primeiro barrel |
| Postes | z=4.6 | **z=6** | Afastados das casas |
| Wire dos postes | z=4.6 | **z=6** | Alinhado com postes |
| Busstop bench | `2.6? 1.2:1.2` | **1.2** | Corrigido ternário inútil |

**Carros:** Adicionado `receiveShadow=true` para melhor integração visual

---

## 2. Resumo das Alterações no Código

### Linha 1062 - Shadow Bias
```javascript
// ANTES
sun.shadow.bias=-.0006

// DEPOIS
sun.shadow.bias=-.001;sun.shadow.normalBias=0.02
```

### Linha 1088 - Material Standard Factory
```javascript
// ANTES
const SM=(map,rough=.9,extra)=>new THREE.MeshStandardMaterial(Object.assign({map,roughness:rough},extra||{}));

// DEPOIS
const SM=(map,rough=.9,extra)=>new THREE.MeshStandardMaterial(Object.assign({map,roughness:rough,polygonOffset:true,polygonOffsetFactor:1,polygonOffsetUnits:1},extra||{}));
```

### Linhas 1093-1104 - Todos os Materiais
Adicionado `polygonOffset:true,polygonOffsetFactor:1,polygonOffsetUnits:1` para:
- grass (factor=2)
- leaf, leafPine, straw, metalR
- dark, white, car, car2
- crate, steel, fabric

### Linhas 1116-1146 - Função houseI()
```javascript
// ANTES (complexo, bugado)
const odd=ry%2===1;
const seg=(lx,lz,len,h,localRy,yC)=>{
  I.walls.add(x,yC??h/2,z,localRy?T:len,h,localRy?len:T,rad+(localRy?Math.PI/2:0));
  const sw=(localRy?1:0)+(odd?1:0);const isSwap=sw%2===1;
  collB(w,x,z,isSwap?T/2+.06:len/2,isSwap?len/2:T/2+.06);
};

// DEPOIS (simples, correto)
const seg=(lx,lz,len,h,localRy,yC)=>{
  const wallRot=rad+(localRy?Math.PI/2:0);
  I.walls.add(x,yC??h/2,z,localRy?T:len,h,localRy?len:T,wallRot);
  const hw=(localRy?T:len)/2,hd=(localRy?len:T)/2;
  collB(w,x,z,hw+.03,hd+.03);
};
```

### Linhas 1179-1196 - Alturas de Superfície
```javascript
// ANTES
const road=add(w,mesh(...,0,.02,0),...);
const path=add(w,mesh(...,8,.02,-14),...);
spl.add(...,.03,...);

// DEPOIS
const road=add(w,mesh(...,0,.05,0),...);
const path=add(w,mesh(...,8,.05,-14),...);
spl.add(...,.06,...);
```

### Linha 1210-1220 - Props Reposicionados
```javascript
// ANTES
well(w,10,3.1);barrel(w,-14.5,7);barrel(w,-13.8,7.4);...
for(let x=-42;x<=42;x+=14)pole(w,x,4.6);
for(let x=-42;x<42;x+=14)box(w,LIB.mat.dark,x+7,5.6,4.6,14,...);

// DEPOIS
well(w,10,5);barrel(w,-14.5,7);barrel(w,-12.5,8.2);...
for(let x=-42;x<=42;x+=14)pole(w,x,6);
for(let x=-42;x<42;x+=14)box(w,LIB.mat.dark,x+7,5.6,6,14,...);
```

### Linha 1157 - Car receiveShadow
```javascript
// ANTES
g.traverse(m=>{if(m.isMesh){w.shootables.push(m);m.castShadow=true;}});

// DEPOIS
g.traverse(m=>{if(m.isMesh){w.shootables.push(m);m.castShadow=true;m.receiveShadow=true;}});
```

### Linha 1175 - Busstop Bench
```javascript
// ANTES
box(w,LIB.mat.crate,x+1.2,.5,z,.06,.5,2.6? 1.2:1.2);

// DEPOIS
box(w,LIB.mat.crate,x+1.2,.5,z,.06,.5,1.2);
```

---

## 3. Métricas de Sucesso - VERIFICAÇÃO PENDENTE

Para validar as correções, execute no browser:
```javascript
// No console do browser com o jogo aberto:
window.__dbg.validateVila()
// Deve retornar: {ok: true, errors: [], rules: {...}, time: <X>}
```

Checklist:
- [ ] Zero flickering visual (z-fight eliminado)
- [ ] Todas as 9 casas com paredes orientadas corretamente
- [ ] Portas e janelas nas paredes apropriadas
- [ ] Objetos alinhados com contexto (estrada, casas, etc.)
- [ ] validateVila() retorna ok=true com 0 errors
- [ ] Performance mantida (>60fps em hardware médio)

---

## 4. Notas Técnicas

### Three.js Specifics
- InstancedMesh herda polygonOffset do material
- polygonOffset funciona em conjunto com depth testing
- shadow.bias negativo puxa sombra para perto, reduzindo acne
- normalBias previne light bleeding em superfícies adjacentes

### Coordinate System
- Y+ = cima, Y- = baixo
- X+ = direita, Z+ = "norte" (profundidade)
- Rotação Y: 0=sul, 1=oeste, 2=norte, 3=leste (em PI/2)

### Ordem de Renderização com PolygonOffset
- Factor=0, Units=0: chão, estruturas principais
- Factor=1, Units=1: maioria dos objetos (paredes, props)
- Factor=2, Units=1: grama, vegetação rasteira
