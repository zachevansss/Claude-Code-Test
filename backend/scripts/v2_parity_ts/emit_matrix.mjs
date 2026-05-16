// Emit a matrix of real V2-signed orders from @polymarket/clob-client-v2.
// Output: one JSON line per case, each {label, negRisk, signed}.
// Consumed by ../probe_v2_hash_parity.py.
import { OrderBuilder, SignatureTypeV2, Side } from '@polymarket/clob-client-v2';
import { Wallet } from 'ethers';

const PRIV = '0x' + '11'.repeat(32);  // test-only deterministic key
const wallet = new Wallet(PRIV);
const FUNDER = '0xB386c5d402171c97Fe3a191F9525F059e2F48550';
const TOKEN = '24222287655595070047137705703317562422872502671827067056744604288762248590543';

const cases = [
    { label: 'BUY tick=0.01 px=0.01 sz=50',         args: [{tokenID:TOKEN, price:0.01,  size:50,  side:Side.BUY},  {tickSize:'0.01',  negRisk:false}, 2] },
    { label: 'SELL tick=0.01 px=0.5 sz=10',         args: [{tokenID:TOKEN, price:0.50,  size:10,  side:Side.SELL}, {tickSize:'0.01',  negRisk:false}, 2] },
    { label: 'BUY tick=0.001 px=0.123 sz=7',        args: [{tokenID:TOKEN, price:0.123, size:7,   side:Side.BUY},  {tickSize:'0.001', negRisk:false}, 2] },
    { label: 'BUY negRisk tick=0.01 px=0.5 sz=4',   args: [{tokenID:TOKEN, price:0.50,  size:4,   side:Side.BUY},  {tickSize:'0.01',  negRisk:true},  2] },
    { label: 'SELL negRisk tick=0.01 px=0.3 sz=20', args: [{tokenID:TOKEN, price:0.30,  size:20,  side:Side.SELL}, {tickSize:'0.01',  negRisk:true},  2] },
];

for (const c of cases) {
    const builder = new OrderBuilder(wallet, 137, SignatureTypeV2.POLY_PROXY, FUNDER);
    const signed = await builder.buildOrder(...c.args);
    const negRisk = c.args[1].negRisk;
    console.log(JSON.stringify({ label: c.label, negRisk, signed }));
}
