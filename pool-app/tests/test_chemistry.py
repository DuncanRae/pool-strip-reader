import copy, datetime as dt, pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import chemistry as c

S = c.DEFAULT_SETTINGS


def readings(fc, ta, ph, cya, flags=None):
    out = {'fc': {'value': fc}, 'ta': {'value': ta}, 'ph': {'value': ph}, 'cya': {'value': cya}}
    for key, extra in (flags or {}).items(): out[key].update(extra)
    return out


def step(analysis, key):
    return next((s for s in analysis['steps'] if s['key'] == key), None)


class Dosing(unittest.TestCase):
    def test_balanced_water_needs_nothing(self):
        a = c.analyse(readings(2, 100, 7.4, 40), S)
        self.assertEqual((a['steps'], a['notes'], a['issues'], a['retestHours']), ([], [], [], None))
        self.assertEqual(a['headline'], 'Water is balanced')

    def test_chlorine_dose_is_a_mass_balance(self):
        # No stabiliser: the range is 1-3, so 0.5 is topped up by 2.5 ppm.
        # 2.5 ppm into 120 000 L is 300 g of chlorine; the product is 60% chlorine.
        a = c.analyse(readings(0.5, 100, 7.4, 0), S)
        self.assertEqual(step(a, 'fc-up')['amount'], 500)
        self.assertIn('2 cups', step(a, 'fc-up')['amountText'])
        self.assertAlmostEqual(c.chlorine_grams(1, 10000, 100), 10)

    def test_alkalinity_and_stabiliser_doses(self):
        self.assertAlmostEqual(c.bicarb_grams(10, 120000), 2014.8, places=0)
        self.assertAlmostEqual(c.stabiliser_grams(10, 120000), 1200)
        a = c.analyse(readings(2, 60, 7.4, 40), S)      # aims at 90: 30 ppm into 120 000 L
        self.assertAlmostEqual(step(a, 'ta-up')['amount'], 6000, delta=100)
        halved = c.analyse(readings(2, 100, 7.4, 0), S)
        self.assertEqual(step(halved, 'cya-up')['amount'], 2400)   # half of 4.8 kg
        self.assertEqual(halved['retestHours'], 168)

    def test_acid_model_reproduces_the_published_worked_example(self):
        # Wojtowicz: taking pH from 8.0 to 7.4 at 100 ppm carbonate alkalinity and
        # 50 ppm cyanuric acid lowers total alkalinity by 8.6 ppm and carbonate
        # alkalinity by 5.8 ppm.
        ta = 100.0
        for _ in range(60): ta += 100 - c.carbonate_alkalinity(8.0, ta, 50)
        drop = c.acid_equivalents(8.0, 7.4, ta, 50) * c.TA_PER_EQ
        self.assertAlmostEqual(drop, 8.6, delta=0.3)
        self.assertAlmostEqual(100 - c.carbonate_alkalinity(7.4, ta - drop, 50), 5.8, delta=0.3)
        self.assertEqual(c.acid_equivalents(7.2, 7.4, 100, 0), 0)

    def test_standard_reference_doses_per_10000_us_gallons(self):
        litres = 37854
        ml, unit = c.acid_amount(10 / c.TA_PER_EQ * litres, {'kind': 'liquid', 'strength': 31.45})
        self.assertEqual(unit, 'ml'); self.assertAlmostEqual(ml / 29.57, 25.6, delta=0.3)    # fl oz of acid per 10 ppm alkalinity
        grams, unit = c.acid_amount(10 / c.TA_PER_EQ * litres, {'kind': 'dry', 'strength': 93})
        self.assertEqual(unit, 'g'); self.assertAlmostEqual(grams, 977, delta=15)
        self.assertAlmostEqual(c.bicarb_grams(10, litres) / 453.6, 1.4, delta=0.02)          # lb of bicarbonate per 10 ppm alkalinity
        self.assertAlmostEqual(c.chlorine_grams(1, litres, 65) / 28.35, 2.0, delta=0.1)      # oz of 65% cal hypo per ppm
        self.assertAlmostEqual(c.stabiliser_grams(10, litres) / 28.35, 13, delta=0.5)        # oz of stabiliser per 10 ppm

    def test_acid_is_capped_per_application(self):
        a = c.analyse(readings(2, 100, 8.4, 40, {'ph': {'atMax': True}}), S)
        acid = step(a, 'ph-down')
        self.assertLessEqual(acid['amount'], 2400)
        self.assertIn('tops out at 8.4', acid['why'])

    def test_stronger_acid_means_a_smaller_dose(self):
        weak = copy.deepcopy(S); weak['products']['acid']['strength'] = 15
        strong = c.analyse(readings(2, 100, 7.9, 40), S)
        self.assertGreater(step(c.analyse(readings(2, 100, 7.9, 40), weak), 'ph-down')['amount'], step(strong, 'ph-down')['amount'])

    def test_alkalinity_comes_before_ph_and_chlorine_last(self):
        a = c.analyse(readings(0.5, 60, 7.9, 40), S)
        self.assertEqual([s['key'] for s in a['steps']], ['ta-up', 'ph-down', 'fc-up'])
        self.assertTrue(a['safety'])
        self.assertEqual(a['retestHours'], 24)

    def test_low_ph_with_low_alkalinity_fixes_alkalinity_only(self):
        a = c.analyse(readings(2, 40, 6.8, 40), S)
        self.assertEqual([s['key'] for s in a['steps']], ['ta-up'])
        self.assertTrue(any('do that first' in n for n in a['notes']))

    def test_low_ph_with_good_alkalinity_uses_soda_ash(self):
        a = c.analyse(readings(2, 110, 6.9, 40), S)
        self.assertEqual(step(a, 'ph-up')['product'], 'phUp')
        self.assertLessEqual(step(a, 'ph-up')['amount'], 1800)

    def test_high_chlorine_and_stabiliser_never_add_product(self):
        a = c.analyse(readings(10, 100, 7.4, 150), S)
        self.assertEqual(a['steps'], [])
        self.assertTrue(any('keep swimmers out' in n for n in a['notes']))
        self.assertTrue(any('67%' in n for n in a['notes']))

    def test_stabiliser_is_not_trusted_outside_its_ph_window(self):
        a = c.analyse(readings(2, 40, 6.6, 0), S)
        self.assertIsNone(step(a, 'cya-up'))
        self.assertTrue(any('7.0 and 8.4' in n for n in a['notes']))

    def test_an_in_range_stabiliser_reading_is_still_qualified_outside_its_ph_window(self):
        a = c.analyse(readings(1.5, 80, 6.7, 30), S)
        self.assertEqual(a['status']['cya'], 'ok')
        caution = [n for n in a['notes'] if 'Stabiliser read 30 ppm' in n]
        self.assertEqual(len(caution), 1)
        self.assertIn('pH is 6.7', caution[0])
        # Inside the window there is nothing to qualify, and an unclear pad says nothing about itself.
        self.assertFalse(any('Stabiliser read' in n for n in c.analyse(readings(2, 90, 7.4, 30), S)['notes']))
        self.assertFalse(any('Stabiliser read' in n for n in c.analyse(readings(1.5, 80, 6.7, 30, {'cya': {'confidence': 'poor'}}), S)['notes']))

    def test_bigger_pool_bigger_dose(self):
        small = copy.deepcopy(S); small['volumeLitres'] = 60000
        self.assertEqual(step(c.analyse(readings(0.5, 100, 7.4, 0), small), 'fc-up')['amount'], 250)

    def test_models_move_the_right_way(self):
        self.assertGreater(c.after_bicarb(7.0, 60, 30, 40), 7.0)
        ph, ta = c.after_soda_ash(7.0, 100, 30, c.soda_ash_moles(7.0, 7.4, 100, 30))
        self.assertAlmostEqual(ph, 7.4, places=2); self.assertGreater(ta, 100)


class PublishedGuidance(unittest.TestCase):
    """Each rule here is tied to a source listed in chemistry.SOURCES."""
    def test_chlorine_minimum_rises_with_stabiliser(self):
        # CDC: 1 ppm, or 2 ppm with stabiliser. Falk 2019: stabiliser at most 20 times the chlorine.
        cases = {0: [1.0, 3.0], 10: [1.0, 3.0], 30: [2.0, 4.0], 40: [2.0, 4.0], 50: [2.5, 4.5], 100: [5.0, 7.0], 150: [7.5, 9.5], 250: [8.0, 10.0]}
        for cya, expected in cases.items():
            self.assertEqual(c.effective_ideal(readings(2, 100, 7.4, cya), S)['fc'], expected, cya)
        a = c.analyse(readings(2, 100, 7.4, 50), S)          # fine on the bottle's chart, too low for 50 ppm stabiliser
        self.assertEqual((a['status']['fc'], step(a, 'fc-up')['amount']), ('low', 500))
        self.assertIn('twentieth', step(a, 'fc-up')['why'])
        self.assertEqual(c.analyse(readings(4, 100, 7.4, 50), S)['status']['fc'], 'ok')    # "high" on the bottle, right for this pool
        self.assertEqual(c.analyse(readings(4, 100, 7.4, 0), S)['status']['fc'], 'high')

    def test_stabiliser_reading_is_only_used_inside_its_ph_window(self):
        self.assertEqual(c.chlorine_basis(readings(2, 100, 6.8, 0)), 'unknown')
        self.assertEqual(c.effective_ideal(readings(2, 100, 6.8, 0), S)['fc'], [2.0, 4.0])
        poor = readings(2, 100, 7.4, 0); poor['cya']['confidence'] = 'poor'
        self.assertEqual(c.chlorine_basis(poor), 'unknown')

    def test_ph_is_left_alone_inside_the_accepted_range(self):
        for ph in (7.2, 7.6, 7.8):
            self.assertEqual(c.analyse(readings(3, 100, ph, 40), S)['steps'], [], ph)
        a = c.analyse(readings(3, 100, 7.9, 40), S)
        self.assertEqual(step(a, 'ph-down')['title'], 'Lower pH')
        self.assertIn('modest effect', step(a, 'ph-down')['why'])           # with stabiliser
        self.assertIn('one spot', step(a, 'ph-down')['how'])
        self.assertIn('killing power', step(c.analyse(readings(3, 100, 8.2, 0), S), 'ph-down')['why'])   # without

    def test_alkalinity_is_only_lowered_above_the_accepted_maximum(self):
        a = c.analyse(readings(3, 150, 7.5, 40), S)
        self.assertEqual((a['steps'], a['issues'], a['headline']), ([], [], 'Water is balanced'))
        self.assertIn('nothing to fix', a['notes'][0])
        self.assertEqual(c.analyse(readings(3, 200, 7.5, 40), S)['status']['ta'], 'high')
        a = c.analyse(readings(3, 200, 7.5, 40), S)
        self.assertEqual(step(a, 'ph-down')['title'], 'Lower alkalinity')
        self.assertIn('aerating', step(a, 'ph-down')['why'])
        top_up = c.analyse(readings(3, 70, 7.5, 40), S)
        self.assertIn('top-up', step(top_up, 'ta-up')['why'])
        self.assertIn('too little buffer', step(c.analyse(readings(3, 40, 7.5, 40), S), 'ta-up')['why'])

    def test_high_chlorine_is_only_a_hazard_at_the_top_of_the_strip(self):
        self.assertIn('not a hazard', c.analyse(readings(6, 100, 7.4, 40), S)['notes'][0])
        self.assertIn('keep swimmers out', c.analyse(readings(10, 100, 7.4, 40), S)['notes'][0])
        zero = step(c.analyse(readings(0, 100, 7.4, 40), S), 'fc-up')
        self.assertIn('bleached', zero['why'])

    def test_no_folklore_in_the_advice(self):
        seen = []
        for values in ((0, 40, 6.6, 0), (0.5, 100, 8.4, 50), (10, 240, 8.4, 250), (3, 200, 7.5, 40), (2, 110, 6.9, 30), (6, 150, 7.7, 100)):
            a = c.analyse(readings(*values), S)
            seen += [s['why'] + ' ' + s['how'] for s in a['steps']] + a['notes']
        text = ' '.join(seen).lower()
        for phrase in ('shock', 'stings', 'sluggish', 'by lunchtime', 'locks up'):
            self.assertNotIn(phrase, text)

    def test_every_source_has_a_link_and_says_what_it_was_used_for(self):
        self.assertGreaterEqual(len(c.SOURCES), 6)
        for source in c.SOURCES:
            self.assertTrue(source['url'].startswith('https://') and source['title'] and source['used'])


class Unclear(unittest.TestCase):
    """A poor colour match must never turn into a dose."""
    def poor(self, values, *keys):
        r = readings(*values)
        for key in keys: r[key]['confidence'] = 'poor'
        return c.analyse(r, S)

    def test_an_unclear_reading_gets_no_advice_of_its_own(self):
        a = self.poor((0.5, 100, 6.4, 40), 'ph')
        self.assertEqual([s['key'] for s in a['steps']], ['fc-up'])
        self.assertEqual((a['status']['ph'], a['unsure'], a['issues']), ('unsure', ['ph'], ['fc']))
        self.assertIn('pH pad did not match', a['notes'][0])
        self.assertNotIn('acid', a['steps'][0]['why'])

    def test_doses_that_depend_on_it_are_held_back_too(self):
        # The acid amount depends on alkalinity; stabiliser is only valid inside a pH window.
        a = self.poor((2, 100, 8.0, 40), 'ta')
        self.assertEqual(a['steps'], [])
        self.assertIn('Advice for pH is held back too', a['notes'][0])
        a = self.poor((2, 100, 7.4, 0), 'ph')
        self.assertEqual(a['steps'], [])
        self.assertIn('Advice for stabiliser is held back too', a['notes'][0])

    def test_the_first_real_test_no_longer_doses_on_its_ph_guess(self):
        a = self.poor((3.5, 40, 6.4, 0), 'ph', 'cya')
        self.assertEqual([s['key'] for s in a['steps']], ['ta-up'])
        self.assertNotIn('nudges pH', a['steps'][0]['why'])
        self.assertEqual(a['headline'], 'Total alkalinity low')
        self.assertEqual((a['chlorineBasis'], a['ideal']['fc']), ('unknown', [2.0, 4.0]))

    def test_headlines_and_set_by_eye(self):
        self.assertEqual(self.poor((2, 100, 7.4, 40), 'ph')['headline'], 'pH unclear')
        self.assertEqual(self.poor((2, 100, 7.4, 40), 'fc', 'ta', 'ph')['headline'], '3 readings unclear')
        r = readings(2, 100, 8.0, 40); r['ph']['confidence'] = 'manual'
        self.assertEqual([s['key'] for s in c.analyse(r, S)['steps']], ['ph-down'])
        self.assertEqual(c.reading_status('ph', {'value': 6.4, 'confidence': 'poor'}, S['ideal']), 'unsure')
        self.assertEqual(c.reading_status('ph', {'value': 6.4, 'confidence': 'fair'}, S['ideal']), 'low')


class Balance(unittest.TestCase):
    def test_textbook_balanced_water(self):
        self.assertAlmostEqual(c.saturation_index(7.5, 100, 0, 250, 25, 1000), -0.09, places=2)

    def test_needs_calcium_hardness(self):
        self.assertFalse(c.balance({'ph': 7.4, 'ta': 100, 'cya': 40}, S)['available'])
        known = copy.deepcopy(S); known['calciumHardness'] = 120
        self.assertEqual(c.balance({'ph': 7.0, 'ta': 60, 'cya': 40}, known, 15)['state'], 'corrosive')
        known['calciumHardness'] = 600
        self.assertEqual(c.balance({'ph': 8.2, 'ta': 180, 'cya': 0}, known, 30)['state'], 'scaling')


class Surface(unittest.TestCase):
    """The pool surface changes handling advice and what the balance index means, never an amount."""
    def settings(self, surface, calcium=None):
        s = copy.deepcopy(S); s.update(surface=surface, calciumHardness=calcium)
        return s

    def test_amounts_do_not_depend_on_the_surface(self):
        for values in ((0.5, 60, 8.2, 10), (3, 100, 6.8, 40)):
            amounts = [[(s['key'], s['amount']) for s in c.analyse(readings(*values), self.settings(surface))['steps']] for surface in c.SURFACES]
            self.assertTrue(amounts[0] and amounts[0] == amounts[1] == amounts[2])

    def test_granules_are_never_left_lying_on_a_surface_they_bleach(self):
        plaster = step(c.analyse(readings(0.5, 100, 7.4, 40), S), 'fc-up')
        self.assertIn('scatter', plaster['how'])
        for surface, word in (('fibreglass', 'gelcoat'), ('vinyl', 'a liner')):
            added = step(c.analyse(readings(0.5, 100, 7.4, 40), self.settings(surface)), 'fc-up')
            self.assertNotIn('scatter', added['how'])
            self.assertIn(f'Do not let granules settle: undissolved chlorine bleaches {word}.', added['how'])
            self.assertEqual('care guide' in added['why'], surface == 'fibreglass')

    def test_plaster_is_only_mentioned_for_a_plaster_pool(self):
        for surface in ('fibreglass', 'vinyl'):
            s = self.settings(surface)
            texts = [x['how'] + x['why'] for values in ((0.5, 100, 8.2, 40), (3, 100, 6.8, 40)) for x in c.analyse(readings(*values), s)['steps']]
            self.assertTrue(texts and not any('plaster' in text for text in texts), texts)
            self.assertNotIn('etching or scaling your plaster', c.balance({'ph': 7.4, 'ta': 100, 'cya': 40}, s)['text'])
        hungry = c.balance({'ph': 7.0, 'ta': 60, 'cya': 40}, self.settings('fibreglass', 120), 15)
        self.assertEqual(hungry['state'], 'corrosive'); self.assertIn('care guide', hungry['text'])
        scaling = c.balance({'ph': 8.2, 'ta': 180, 'cya': 0}, self.settings('fibreglass', 600), 30)
        self.assertEqual(scaling['state'], 'scaling'); self.assertIn('discolours gelcoat', scaling['text'])
        self.assertNotIn('gelcoat', c.balance({'ph': 8.2, 'ta': 180, 'cya': 0}, self.settings('gunite', 600), 30)['text'])

    def test_an_unknown_surface_is_treated_as_plaster(self):
        self.assertIn('scatter', step(c.analyse(readings(0.5, 100, 7.4, 40), self.settings('mystery')), 'fc-up')['how'])


class Trends(unittest.TestCase):
    def history(self, rows):
        base = dt.datetime(2026, 11, 1, 8, tzinfo=dt.timezone.utc)
        return [{'id': str(i), 'at': (base + dt.timedelta(days=day)).isoformat(), 'readings': readings(fc, 100, ph, 40)} for i, (day, fc, ph) in enumerate(rows)]

    def test_drift_ignores_stretches_with_a_dose(self):
        tests = self.history([(0, 3, 7.2), (2, 2, 7.3), (4, 1, 7.4), (6, 3, 7.5)])
        dose = {'at': (dt.datetime(2026, 11, 6, 8, tzinfo=dt.timezone.utc)).isoformat(), 'product': 'chlorine', 'amount': 500}
        self.assertAlmostEqual(c.drift(tests, [dose], 'fc'), -0.5)
        self.assertAlmostEqual(c.drift(tests, [dose], 'ph'), 0.05)
        text = ' '.join(t['text'] for t in c.trends(tests, [dose], S))
        self.assertIn('0.5 ppm of chlorine a day', text); self.assertIn('pH drifts up about 0.3 a week', text)

    def test_a_dose_on_the_edge_of_a_gap_counts_as_inside_it_and_a_long_history_stays_quick(self):
        day = lambda n: (dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(days=n)).isoformat()
        tests = [{'at': day(n), 'readings': readings(3, 100, 7.2 + 0.1 * n, 40)} for n in range(4)]
        self.assertAlmostEqual(c.drift(tests, [], 'ph'), 0.1)
        # Doses exactly at the start and at the end of the middle gap, and one before everything, which touches nothing.
        self.assertIsNone(c.drift(tests, [{'at': day(1), 'product': 'acid'}, {'at': day(-5), 'product': 'acid'}], 'ph'))
        self.assertIsNone(c.drift(tests, [{'at': day(2), 'product': 'acid'}], 'ph'))
        self.assertAlmostEqual(c.drift(tests, [{'at': day(-5), 'product': 'acid'}, {'at': day(9), 'product': 'acid'}, {'at': day(1), 'product': 'chlorine'}], 'ph'), 0.1)
        many = [{'at': day(n), 'readings': readings(3, 100, 7.4, 40)} for n in range(5000)]
        doses = [{'at': day(-n - 1), 'product': 'acid'} for n in range(5000)]
        import time
        started = time.perf_counter(); c.trends(many, doses, S)
        self.assertLess(time.perf_counter() - started, 2.0)   # was tens of seconds before the doses were searched, not scanned

    def test_too_little_history_says_nothing(self):
        self.assertEqual(c.trends(self.history([(0, 3, 7.2), (2, 2, 7.3)]), [], S), [])


class ShopTests(unittest.TestCase):
    """A shop's test can be entered days after its sample was taken. It must take its place in time and disturb nothing newer."""
    CHART = __import__('json').loads((pathlib.Path(__file__).resolve().parent.parent / 'chart.json').read_text())
    at = staticmethod(lambda hours: (dt.datetime(2026, 11, 13, 10, 40, tzinfo=dt.timezone.utc) + dt.timedelta(hours=hours)).isoformat())

    def sample(self, ident, hours, fc, ta, ph, cya, source='photo', flags=None):
        return {'id': ident, 'at': self.at(hours), 'source': source, 'readings': readings(fc, ta, ph, cya, flags)}

    def test_no_amounts_are_given_from_a_test_once_chemicals_have_gone_in_after_it(self):
        shop = self.sample('shop', 0, 3.86, 67, 7.3, 42, 'lab')
        plan = lambda doses: c.hold_if_dosed_since(c.analyse(shop['readings'], S), shop, doses, S)
        self.assertEqual(([s['key'] for s in plan([])['steps']], plan([]).get('dosedSince')), (['ta-up'], None))
        earlier = {'at': self.at(-3), 'product': 'alkalinityUp', 'amount': 4000, 'testId': 'strip'}
        own = {'at': self.at(2), 'product': 'alkalinityUp', 'amount': 4000, 'testId': 'shop'}           # ticked off from this test's own plan
        undone = {'at': self.at(5), 'product': 'acid', 'amount': 500, 'testId': 'strip', 'voidedAt': self.at(6)}
        self.assertEqual([s['key'] for s in plan([earlier, own, undone])['steps']], ['ta-up'])
        later = {'at': self.at(5), 'product': 'alkalinityUp', 'amount': 4000, 'testId': 'strip'}
        held = plan([earlier, later, {**later, 'at': self.at(7)}, {'at': self.at(8), 'product': 'acid', 'amount': 500, 'testId': 'strip'}])
        self.assertEqual((held['steps'], held['dosedSince'], held['retestHours'], held['safety']), ([], True, None, None))
        self.assertEqual((held['status']['ta'], held['headline']), ('low', 'Total alkalinity low'))   # what the sample showed still stands
        self.assertTrue(held['notes'][0].startswith('HTH Alkalinity Up and Pool acid were added after this sample was taken'), held['notes'][0])
        self.assertIn('No amounts are given from this test', held['notes'][0])
        balanced = self.sample('fine', 0, 3, 100, 7.4, 40, 'lab')
        note = c.hold_if_dosed_since(c.analyse(balanced['readings'], S), balanced, [later], S)['notes'][0]
        self.assertTrue(note.startswith('HTH Alkalinity Up was added after') and 'No amounts' not in note, note)

    def test_a_shop_test_between_two_strips_is_not_read_as_the_pool_drifting(self):
        day = lambda n: (dt.datetime(2026, 11, 1, 8, tzinfo=dt.timezone.utc) + dt.timedelta(days=n)).isoformat()
        strips = [{'id': str(n), 'at': day(n), 'source': 'photo', 'readings': readings(3 - 0.5 * n, 90, 7.2 + 0.05 * n, 40)} for n in (0, 2, 4, 6)]
        shop = {'id': 'shop', 'at': day(3), 'source': 'lab', 'readings': readings(4.2, 62, 7.0, 55)}   # the same water, read a colour step apart
        for key in c.PARAMS: self.assertEqual(c.drift(strips + [shop], [], key), c.drift(strips, [], key), key)
        self.assertEqual(c.trends(strips + [shop], [], S)[1:], c.trends(strips, [], S)[1:])

    def test_a_strip_is_allowed_half_a_colour_step(self):
        verdict = lambda key, strip, shop, **around: c.strip_verdict(key, {'value': strip, **around}, shop, self.CHART)
        self.assertEqual([verdict('ph', strip, 7.3) for strip in (7.0, 7.1, 7.2, 7.5, 7.6)], ['lower', 'agrees', 'agrees', 'agrees', 'higher'])   # steps 7.2 and 7.6: 0.2 either way
        self.assertEqual([verdict('ta', strip, 67) for strip in (40, 50, 80, 90)], ['lower', 'agrees', 'agrees', 'higher'])                      # steps 40 and 80: 20 either way
        self.assertEqual([verdict('fc', strip, 3.86) for strip in (2.5, 3, 5, 10)], ['lower', 'agrees', 'higher', 'higher'])                     # steps 3 and 5: 1 either way
        self.assertEqual((verdict('ph', 7.6, 7.3, low=7.4, high=7.6), verdict('cya', 50, 42), verdict('cya', 100, 42)), ('agrees', 'agrees', 'higher'))
        # At the end of its chart a strip says "this or beyond", so a shop figure beyond the chart agrees with it.
        self.assertEqual((verdict('fc', 0, 0), verdict('fc', 10, 14), verdict('ph', 8.4, 9), verdict('ph', 6.2, 5.5), verdict('ph', 8.0, 9)), ('agrees', 'agrees', 'agrees', 'agrees', 'lower'))

    def test_strips_are_only_checked_against_a_shop_test_when_the_comparison_is_fair(self):
        shop = self.sample('shop', 0, 3.86, 67, 7.3, 42, 'lab')
        check = lambda tests, doses=(): c.strip_checks([shop] + tests, list(doses), self.CHART)
        verdicts = lambda rows: {key: row['verdict'] for key, row in rows[0]['readings'].items()}
        near = self.sample('near', 3, 3, 90, 7.4, 50)
        only = check([near, self.sample('far', -30, 1, 80, 7.2, 50), {**self.sample('gone', 1, 3, 80, 7.2, 50), 'voidedAt': self.at(2)}])
        self.assertEqual((len(only), only[0]['stripId'], only[0]['hours'], only[0]['stripLater']), (1, 'near', 3.0, True))
        self.assertEqual(verdicts(only), {'fc': 'agrees', 'ta': 'higher', 'ph': 'agrees', 'cya': 'agrees'})
        self.assertEqual(only[0]['readings']['ta'], {'shop': 67, 'strip': 90, 'verdict': 'higher'})
        # Chlorine moves by the hour and pH by the day, so a strip from the day before is only a check on the slow two.
        self.assertEqual(verdicts(check([self.sample('before', -20, 3, 80, 7.2, 50)])), {'fc': 'apart', 'ta': 'agrees', 'ph': 'agrees', 'cya': 'agrees'})
        self.assertEqual(verdicts(check([self.sample('before', -42, 3, 80, 7.2, 50)])), {'fc': 'apart', 'ta': 'agrees', 'ph': 'apart', 'cya': 'agrees'})
        self.assertEqual(check([self.sample('before', -50, 3, 80, 7.2, 50)]), [])
        # Whatever was added between the two tests rules out the readings it moves, and no others.
        acid = {'at': self.at(1), 'product': 'acid', 'amount': 500}
        self.assertEqual(verdicts(check([near], [acid])), {'fc': 'agrees', 'ta': 'dosed', 'ph': 'dosed', 'cya': 'agrees'})
        self.assertEqual(verdicts(check([near], [{**acid, 'at': self.at(4)}, {**acid, 'at': self.at(-1)}, {**acid, 'voidedAt': self.at(2)}])), verdicts(only))
        self.assertEqual(verdicts(check([self.sample('poor', 3, 3, 90, 7.4, 50, flags={'ph': {'confidence': 'poor'}})]))['ph'], 'unclear')
        self.assertEqual((check([]), c.strip_checks([near], [], self.CHART)), ([], []))


if __name__ == '__main__':
    unittest.main()
