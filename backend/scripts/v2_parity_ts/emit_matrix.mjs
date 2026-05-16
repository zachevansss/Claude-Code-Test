// Emit a matrix of real V2-signed orders from @polymarket/clob-client-v2.
// Output: one JSON line per case, each {label, negRisk, signed}.
// Consumed by ../probe_v2_hash_parity.py.
import { OrderBuilder, SignatureTypeV2, Side } from '@polymarket/clob-client-v2';
import { Wallet } from 'ethers';

const PRIV = '0x' + '11'.repeat(32);  // test-only deterministic key
const wallet = new Wallet(PRIV);
const FUNDER = '0xB386c5d402171c97Fe3a191F9525F059e2F48550';
const TOKEN = '24222287655595070047137705703317562422872502671827067056744604288762248590543';

// Mix POLY_PROXY (sig type 1 — V1 flow, kept for regression) and POLY_1271
// (sig type 3 — V2 "deposit wallet flow" for Magic Link / smart-contract
// proxies, which is what production actually uses).
const cases = [
    { label: 'POLY_PROXY BUY tick=0.01 px=0.01 sz=50',    sigType: SignatureTypeV2.POLY_PROXY, args: [{tokenID:TOKEN, price:0.01,  size:50,  side:Side.BUY},  {tickSize:'0.01',  negRisk:false}, 2] },
    { label: 'POLY_PROXY SELL tick=0.01 px=0.5 sz=10',    sigType: SignatureTypeV2.POLY_PROXY, args: [{tokenID:TOKEN, price:0.50,  size:10,  side:Side.SELL}, {tickSize:'0.01',  negRisk:false}, 2] },
    { label: 'POLY_PROXY BUY negRisk px=0.5 sz=4',        sigType: SignatureTypeV2.POLY_PROXY, args: [{tokenID:TOKEN, price:0.50,  size:4,   side:Side.BUY},  {tickSize:'0.01',  negRisk:true},  2] },
    { label: 'POLY_1271 BUY tick=0.01 px=0.01 sz=50',     sigType: SignatureTypeV2.POLY_1271,  args: [{tokenID:TOKEN, price:0.01,  size:50,  side:Side.BUY},  {tickSize:'0.01',  negRisk:false}, 2] },
    { label: 'POLY_1271 SELL tick=0.01 px=0.5 sz=10',     sigType: SignatureTypeV2.POLY_1271,  args: [{tokenID:TOKEN, price:0.50,  size:10,  side:Side.SELL}, {tickSize:'0.01',  negRisk:false}, 2] },
    { label: 'POLY_1271 BUY negRisk px=0.5 sz=4',         sigType: SignatureTypeV2.POLY_1271,  args: [{tokenID:TOKEN, price:0.50,  size:4,   side:Side.BUY},  {tickSize:'0.01',  negRisk:true},  2] },
];

for (const c of cases) {
    const builder = new OrderBuilder(wallet, 137, c.sigType, FUNDER);
    const signed = await builder.buildOrder(...c.args);
    const negRisk = c.args[1].negRisk;
    console.log(JSON.stringify({ label: c.label, sigType: c.sigType, negRisk, signed }));
}
