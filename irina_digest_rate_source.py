"""Read the current public WhatsApp calculator; never cache a signed CDN URL."""
import html
import json
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from html.parser import HTMLParser

PAGE='https://business.whatsapp.com/products/platform-pricing'
API='https://whatsappbusiness.com/wp-json/wab/v1/pricing'
CATEGORIES={'MARKETING':'Marketing','UTILITY':'Utility','SERVICE':'Service'}


class SourceError(ValueError):
    pass


class Page(HTMLParser):
    def __init__(self):
        super().__init__();self.scripts=[];self.contexts=[];self.script=None
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='script':self.script=[]
        if 'data-wp-context' in attrs:
            try:self.contexts.append(json.loads(html.unescape(attrs['data-wp-context'])))
            except ValueError:pass
    def handle_data(self,data):
        if self.script is not None:self.script.append(data)
    def handle_endtag(self,tag):
        if tag=='script' and self.script is not None:
            try:self.scripts.append(json.loads(''.join(self.script)))
            except ValueError:pass
            self.script=None


def configuration(data):
    p=Page();p.feed(data.decode('utf-8'));configs=[];categories=set()
    def walk(value):
        if isinstance(value,dict):
            if 'restUrl' in value:configs.append(value)
            if isinstance(value.get('allowedCategories'),list):
                categories.update(v for v in value['allowedCategories'] if isinstance(v,str))
            for child in value.values():walk(child)
        elif isinstance(value,list):
            for child in value:walk(child)
    for value in p.scripts+p.contexts:walk(value)
    if len(configs)!=1 or not set(CATEGORIES.values())<=categories:
        raise SourceError('OfficialCalculatorAmbiguousOrChanged')
    c=configs[0]
    if (c.get('restUrl')!=API or c.get('marketValueToName',{}).get('IT')!='Italy'
            or 'EUR' not in c.get('currencyValueToLabel',{})
            or any(k in c and (not isinstance(c[k],str) or len(c[k])>256) for k in ['restNonce','wpNonce'])):
        raise SourceError('OfficialCalculatorContextChanged')
    return c


def fetch(url, headers=None, stage='page'):
    try:
        req=urllib.request.Request(url,headers=headers or {})
        with urllib.request.urlopen(req,timeout=20) as r:
            parsed=urllib.parse.urlparse(r.url)
            if parsed.scheme!='https' or parsed.hostname not in {'business.whatsapp.com','whatsappbusiness.com'}:
                raise SourceError('InvalidOfficialCalculatorHost')
            if stage!='page' and parsed.path!='/wp-json/wab/v1/pricing':
                raise SourceError('InvalidOfficialCalculatorEndpoint')
            data=r.read(2_000_001)
        if len(data)>2_000_000:raise SourceError('OfficialCalculatorTooLarge')
        return data
    except urllib.error.HTTPError as e:
        # Never log query parameters (public nonces) or request headers.
        raise SourceError('OfficialCalculator_'+stage+'_HTTP_'+str(e.code)) from None
    except SourceError:raise
    except Exception:raise SourceError('OfficialCalculator_'+stage+'_Unavailable') from None


def current_rates(reader=fetch):
    c=configuration(reader(PAGE,stage='page'));result={}
    for category,label in CATEGORIES.items():
        params={'market':'IT','currency':'EUR','category':label}
        if c.get('restNonce'):params['_wab_nonce']=c['restNonce']
        query=urllib.parse.urlencode(params)
        headers={'X-WP-Nonce':c['wpNonce']} if c.get('wpNonce') else {}
        data=reader(API+'?'+query,headers=headers,stage=category)
        try:
            item=json.loads(data);quote=Decimal(item['quote'])
            if not quote.is_finite() or not 0<quote<1 or not isinstance(item.get('tier_list'),list):
                raise ValueError()
            if item['tier_list']:
                bases=[Decimal(t['quote']) for t in item['tier_list'] if int(t['min_volume'])==0]
                if bases!=[quote]:raise ValueError()
            result[category]=str(quote)
        except Exception:raise SourceError('OfficialCalculator_'+category+'_InvalidQuote') from None
    return result
