-- TransLog test data: 500 customers, 20 000 shipments (made up, no real people)
SET ROLE portal;

CREATE TABLE klanten (
    id serial PRIMARY KEY,
    naam text NOT NULL,
    plaats text NOT NULL,
    email text NOT NULL
);

CREATE TABLE zendingen (
    id serial PRIMARY KEY,
    trackingcode text UNIQUE NOT NULL,
    klant_id int NOT NULL REFERENCES klanten(id),
    van text NOT NULL,
    naar text NOT NULL,
    status text NOT NULL,
    gewicht_kg numeric(7,1) NOT NULL,
    aangemaakt timestamptz NOT NULL,
    bijgewerkt timestamptz NOT NULL
);

INSERT INTO klanten (naam, plaats, email)
SELECT 'Klant ' || g,
       (ARRAY['Eindhoven','Tilburg','Venlo','Breda','Utrecht','Rotterdam','Antwerpen','Duisburg'])[1 + g % 8],
       'klant' || g || '@voorbeeld.nl'
FROM generate_series(1, 500) g;

INSERT INTO zendingen (trackingcode, klant_id, van, naar, status, gewicht_kg, aangemaakt, bijgewerkt)
SELECT 'TL' || lpad(g::text, 8, '0'),
       1 + g % 500,
       (ARRAY['Eindhoven','Venlo','Rotterdam','Antwerpen'])[1 + g % 4],
       (ARRAY['Tilburg','Breda','Utrecht','Duisburg','Keulen','Lyon'])[1 + g % 6],
       (ARRAY['Aangemeld','Opgehaald','Onderweg','In depot','Bezorgd'])[1 + g % 5],
       round((random() * 900 + 1)::numeric, 1),
       now() - (g || ' minutes')::interval,
       now() - ((g / 2) || ' minutes')::interval
FROM generate_series(1, 20000) g;

CREATE INDEX ON zendingen (klant_id);
