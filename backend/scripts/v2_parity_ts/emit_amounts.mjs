// Emit the maker/taker amounts produced by @polymarket/clob-client-v2 for
// a matrix of price/size/side/tick combos. Output: one JSON line per case.
// Consumed by ../probe_v2_hash_parity.py.
import { OrderBuilder, SignatureTypeV2, Side } from '@polymarket/clob-client-v2';
import { Wallet } from 'ethers';

const wallet = new Wallet('0x' + '11'.repeat(32));
const FUNDER = '0xB386c5d402171c97Fe3a191F9525F059e2F48550';
const TOKEN = '24222287655595070047137705703317562422872502671827067056744604288762248590543';

const cases = [
    ['BUY',  0.01,   50,    '0.01'],
    ['SELL', 0.50,   10,    '0.01'],
    ['BUY',  0.123,  7,     '0.001'],
    ['BUY',  0.789,  100,   '0.001'],
    ['SELL', 0.0042, 1234,  '0.0001'],
    ['BUY',  0.999,  1.5,   '0.001'],
];

for (const [side, price, size, tickSize] of cases) {
    const b = new OrderBuilder(wallet, 137, SignatureTypeV2.POLY_PROXY, FUNDER);
    const signed = await b.buildOrder(
        { tokenID: TOKEN, price, size, side: side === 'BUY' ? Side.BUY : Side.SELL },
        { tickSize, negRisk: false },
        2,
    );
    console.log(JSON.stringify({
        side, price, size, tickSize,
        makerAmount: signed.makerAmount,
        takerAmount: signed.takerAmount,
    }));
}
