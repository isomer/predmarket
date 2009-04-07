import wsgiref.handlers

from google.appengine.ext import webapp
from google.appengine.api import users
from google.appengine.ext import db
from google.appengine.ext.webapp import template
import cgi
import datetime

COST_PER_BUNDLE = 100

class Account(db.Model):
	user = db.UserProperty(required=True)
	balance = db.IntegerProperty(default=10000,required=True) # $10000
	created = db.DateTimeProperty(required=True,auto_now_add=True)
	last_credit = db.DateTimeProperty()

class Bundle(db.Model):
	name= db.StringProperty(required=True)
	title= db.StringProperty(required=True)
	description= db.StringProperty(required=True)
	resolvedat= db.DateTimeProperty(required=False,auto_now_add=False)
	resolvedas= db.ReferenceProperty(required=False)

class Stock(db.Model):
	name= db.StringProperty(required=True)
	title= db.StringProperty(required=True)
	description= db.StringProperty(required=True)
	bundle = db.ReferenceProperty(required=True)

class Ownership(db.Model):
	account = db.ReferenceProperty(Account,required=True)
	stock = db.ReferenceProperty(Stock,required=True)
	amount = db.IntegerProperty(required=True)

class Trade(db.Model):
	account = db.ReferenceProperty(Account,required=True)
	stock = db.ReferenceProperty(Stock,required=True)
	amount = db.IntegerProperty(required=True)
	price = db.IntegerProperty(required=True)
	when = db.DateTimeProperty(required=False,auto_now_add=True)

class History(db.Model):
	account1 = db.ReferenceProperty(Account,required=True,
					collection_name="history1_set")
	account2 = db.ReferenceProperty(Account,required=True,
					collection_name="history2_set")
	amount = db.IntegerProperty(required=True)
	price = db.IntegerProperty(required=True)
	when = db.DateTimeProperty(required=True,auto_now_add=True)

def getAmount(stock,account):
	amounts=stock.ownership_set.filter("account",account.key())
	if amounts.count(1)==0:
		return 0
	return amounts[0].amount

def is_admin(player):
	return player.user.nickname() in ["isomer","test@example.com"] or users.is_current_user_admin()

class MainHandler(webapp.RequestHandler):

  def get(self):
	user = users.get_current_user()
	if not user:
		self.redirect(users.create_login_url(self.request.uri))
		return

	player=Account.get_or_insert(key_name=user.nickname(),user=user)

	totalvalue=0
	bundles=[]
	for bundle in Bundle.all():
		bundleinfo = {
			"title" : bundle.title,
			"resolved" : bundle.resolvedas is not None,
			"name" : bundle.name,
			"stock" : [],
			"total" : 0,
		}
		bundletotal=0
		for stock in bundle.stock_set.order('name'): 
			amount=getAmount(stock,player)
			estvalue=COST_PER_BUNDLE/bundle.stock_set.count()
			totestvalue=estvalue*amount
			totalvalue+=estvalue*amount
			bundleinfo["total"]+=estvalue*amount
			bundleinfo["stock"].append({
				"title" : stock.title,
				"admin" : is_admin(player),
				"amount" : amount,
				"estvalue" : estvalue,
				"totestvalue" : totestvalue,
				"resolved" : (bundle.resolvedas and bundle.resolvedas.key() == stock.key())
			})
		bundles.append(bundleinfo)

	self.response.out.write(template.render("index.tmpl",{
			"player" : {
				"name" : player.user.nickname(),
				"balance" : player.balance,
				"admin" : is_admin(player),
				},
			"bundles" : bundles,
			"total" : totalvalue,
		}))


def buy_a_bundle(playerid,stocks,amount):
	player = db.get(playerid)

	if amount > (player.balance)/len(stocks):
		amount = (player.balance)

	player.balance -= amount * COST_PER_BUNDLE

	for (stock,bundlename) in stocks:
		key_name = player.user.nickname()+"/"+bundlename+"/"+stock.name
		ownership = Ownership.get(db.Key.from_path(
				'Account',player.user.nickname(),
				'Ownership',key_name))
		
		if ownership is None:
			ownership = Ownership(parent=player, 
					key_name=key_name,
					account=player,
					stock=stock,
					amount=0)

		ownership.amount += amount
		ownership.put()
		ownership=None


	player.put()

class BundleHandler(webapp.RequestHandler):

  def post(self):
	user = users.get_current_user()
	if not user:
		self.redirect(users.create_login_url(self.request.uri))
		return

	player=Account.get_by_key_name(user.nickname())

	bundlename=self.request.get('bundle')
	if bundlename=='':
		self.redirect('/')
		return

	bundle = Bundle.get_by_key_name(bundlename)

	if bundle is None:
		self.redirect('/')
		return

	# Deal with people buying bundles
	try:
		amount=int(self.request.get('amount','0'))
	except:
		amount=0
	if amount>0 and amount<=player.balance*COST_PER_BUNDLE:
		stocks=[ (x,x.bundle.name) 
			for x in bundle.stock_set
			]
		db.run_in_transaction(buy_a_bundle,
				player.key(), stocks, amount) 

	# Now deal with all the trades
	for stock in bundle.stock_set.order('name'):
		for trade in stock.trade_set:
			if trade.account.key() == player.key():
				# If the user has withdrawn a trade, remove it.
				if self.request.get(str(trade.key())+"_withdraw"):
					self.response.out.write("<!-- Trade withdrawn -->\n")
					trade.delete()
					continue
				else:
					self.response.out.write("<!-- Trade not withdrawn -->\n")
					
			try:
				amount=int(self.request.get(str(trade.key())))
			except:
				continue

			if amount<1:
				continue

			if trade.amount<0:
				amount*=-1

			# I'd use a transaction here, but uhh, I can't.
			# (two entity groups)

			# positive amount == player buys.
			# negative amount == player sells.
			
			trademax = getAmount(stock,trade.account)

			if trademax < amount:
				self.response.out.write("<!-- other party max resource = %d < offered %d-->\n" % (trademax,amount))
				amount = trademax

			if player.balance < amount * trade.price:
				self.response.out.write("<!-- Player can only afford %d (wanted %d) -->\n"
					% (player.balance/trade.price,amount))
				amount = int(trade.price * amount)

			if trade.account.balance < -amount*trade.price:
				self.response.out.write("<!-- Trader can only afford %d (wanted %d) -->\n"
					% (trade.account.balance / trade.price))
				amount = -int(trade.account.balance / trade.price)

			playeramount = getAmount(stock,player)
			if playeramount < -amount:
				self.response.out.write("<!-- Player's max resource = %d < offered %d -->\n" % (playeramount,amount))
				amount = -playeramount

			self.response.out.write("<!-- new amount = %d -->\n" % amount)
			if amount==0:
				continue

			if trade.account.key() == player.key():
				player2 = player
			else:
				player2 = trade.account

			# Figure out the maximum we can trade
			key_name = (player.user.nickname()
					+"/"+bundle.name
					+"/"+stock.name)

			playerownership = Ownership.get_or_insert(
					parent=player,
					key_name=key_name,
					account = player,
					stock = stock,
					amount = 0)

			# Figure out the maximum we can trade
			key_name = (trade.account.user.nickname()
					+"/"+bundle.name
					+"/"+stock.name)
			traderownership = Ownership.get_or_insert(
					parent = player2,
					key_name=key_name,
					account = player2,
					stock = stock,
					amount = 0)

			# If a player is trading with themself, then don't get confused.
			if playerownership.key() == traderownership.key():
				traderownership = playerownership

			# Perform the trade
			player2.balance += amount * trade.price
			player.balance -= amount * trade.price
			trade.amount -= amount
			key_name = (player.user.nickname()
					+"/"+bundle.name
					+"/"+stock.name)

			playerownership.amount += amount
			traderownership.amount -= amount

			history = History(
					account1=player2,
					account2=player,
					amount=amount,
					price=trade.price)

			# And save everything (pray that it helps)
			player2.put()
			player.put()
			trade.put()
			traderownership.put()
			playerownership.put()

			self.response.out.write("<!-- amount=%d @ %d -->\n" % (amount,trade.price))

			if traderownership.amount == 0:
				traderownership.delete()

			if playerownership.amount == 0:
				playerownership.delete()

			if trade.amount == 0:
				trade.delete()

	# And now try creating new trades
	for stock in bundle.stock_set:
		try:
			amount=int(self.request.get(stock.name+"_amount"))
			price=float(self.request.get(stock.name+"_price"))
		except:
			amount=0
			price=0
		# create a new trade
		if amount>0 and price>0:
			if self.request.get(stock.name+"_buy_sell")=="buy":
				amount*=-1
			trade=Trade(
				account=player,
				stock=stock,
				amount=amount,
				price=int(price))
			trade.put()

  	return self.do_stuff()

  def get(self):
  	return self.do_stuff()

  def do_stuff(self):
	user = users.get_current_user()
	# Don't require auth for this page
	if user:
		player=Account.get_by_key_name(user.nickname())
	else:
		player=None

	bundlename=self.request.get('bundle')
	if bundlename=='':
		self.redirect('/')
		return

	bundle = Bundle.get_by_key_name(bundlename)

	if bundle is None:
		self.redirect('/')
		return

	formatvars = {
		"bundle" : {
			"name" : bundle.name,
			"title" : bundle.title,
			"description" : bundle.description,
			"resolved" : bundle.resolvedas is not None,
			"stocks" : [],
		},
		"player" : {
			"name" : player and player.user.nickname(),
			"balance" : player and player.balance,
			"afford_bundle" : player and player.balance > COST_PER_BUNDLE,
		},
	}

	for stock in bundle.stock_set:
		if player:
			keyid=(
				player.user.nickname()
				+"/"+bundle.name
				+"/"+stock.name)
			ownership=Ownership.get(db.Key.from_path(
				'Account',player.user.nickname(),
				'Ownership',keyid))
		else:
			ownership=None
		stockinfo = {
			"resolved" : bundle.resolvedas and bundle.resolvedas.key() == stock.key(),
			"owned" : ownership and ownership.amount,
			"name" : stock.name,
			"title" : stock.title,
			"description" : stock.description,
			"trades" : [],
			}
		for i in stock.trade_set.order('price'):
			if i.amount < 0:
				tradetype="Buy"
			else:
				tradetype="Sell"
			stockinfo["trades"].append({
				"nickname" : i.account.user.nickname(),
				"type" : tradetype,
				"magnitude" : abs(i.amount),
				"price" : i.price,
				"id" : i.key(),
			})
		formatvars["bundle"]["stocks"].append(stockinfo)

	self.response.out.write(template.render("bundle.tmpl",formatvars))

class NewBundleHandler(webapp.RequestHandler):

  def get(self):
	user = users.get_current_user()
	if not user:
		self.redirect(users.create_login_url(self.request.uri))
		return

	if user.nickname() not in ["isomer","test@example.com"] and not users.is_current_user_admin():
		self.response.out.write("%s not authorised" % user.nickname())
		return

	self.response.out.write("""
<form method=POST>
 Bundle ID: <input type="text" name="name" size=8><br>
 Bundle Title: <input type="text" name="title"><br>
 Bundle Description:<br>
 <textarea cols=80 name="description"></textarea><br>
""")
	for i in range(50):
		self.response.out.write("""
 <hr>
 Stock %(num)d ID: <input type="text" name="name_%(num)d" size=8><br>
 Stock %(num)d Title: <input type="text" name="title_%(num)d"><br>
 Stock %(num)d Description:<br>
 <textarea cols=80 name="description_%(num)d"></textarea><br>
""" % { "num" : i })
	self.response.out.write("""<input type="submit" value="Create Bundle">""")

  def post(self):
	user = users.get_current_user()
	if not user:
		self.redirect(users.create_login_url(self.request.uri))
		return

	if user.nickname() not in ["isomer","test@example.com"] and not users.is_current_user_admin():
		self.response.out.write("%s not authorised" % user.nickname())
		return

	bundle=Bundle(key_name=self.request.get('name'),
			name=self.request.get('name'),
			title=self.request.get('title'),
			description=self.request.get('description'))

	bundle.put()

	for i in range(50):
		if self.request.get('name_%d' % i) is not None and len(self.request.get('name_%d' % i))!=0:
			stock=Stock(name=self.request.get('name_%d' % i),
					title=self.request.get('title_%d' % i),
					description=self.request.get('description_%d' % i),
					parent=bundle,
					bundle=bundle)
			stock.put()

	self.redirect("/")
  	
class ResolveHandler(webapp.RequestHandler):

  def get(self):
	user = users.get_current_user()
	if not user:
		self.redirect(users.create_login_url(self.request.uri))
		return

	if user.nickname() not in ["isomer","test@example.com"] and not users.is_current_user_admin():
		self.response.out.write("%s not authorised" % user.nickname())
		return

	bundlename=self.request.get('bundle')
	if bundlename=='':
		self.redirect('/')
		return

	bundle = Bundle.get_by_key_name(bundlename)

	if bundle is None:
		self.redirect('/')
		return

	if bundle.resolvedat is not None:
		self.response.out.write("This bundle was resolved at %s " % bundle.resolvedat)
		return
	
	self.response.out.write("<h1>%s: %s</h1>\n" % (cgi.escape(bundle.name), cgi.escape(bundle.title)))
	self.response.out.write("<form method=\"POST\">\n");
	self.response.out.write('<input type=\"hidden\" value=\"%s\">' % cgi.escape(bundle.name))
	self.response.out.write("<table>\n")
	for stock in bundle.stock_set.order('name'):
		self.response.out.write("<tr><td><input type=\"radio\" name=\"resolve\" value=\"%s\"></td><td>%s: %s</td></tr>\n" %
				(cgi.escape(stock.name),cgi.escape(stock.name),cgi.escape(stock.title)))
	self.response.out.write("</table>\n")
	self.response.out.write("<input type=\"submit\" value=\"Resolve\">\n")
	self.response.out.write("</form>\n")

  def post(self):
	bundlename = self.request.get('bundle')
	stockname = self.request.get('resolve')
  	# Should be in a transaction too

	# Make sure this hasn't already been resolved
	bundle = Bundle.get_by_key_name(bundlename)
	if bundle is None:
		self.response.out.write("Unknown bundle")
		return

	stock=bundle.stock_set.filter("name =",stockname).get()
	if stock is None:
		self.response.out.write("Unknown stock to resolve to")
		return

	if bundle.resolvedat is not None:
		self.response.out.write("Already resolved!")
		return

	if stock.bundle.key() != bundle.key():
		self.response.out.write("%s not part of bundle %s (%s)" % (
			stock.name,bundle.name,stock.bundle.name))
		return

	# Resolve it for now
	bundle.resolvedat = datetime.datetime.now()
	bundle.resolvedas = stock
	bundle.put()
	# Everyone that owns this stock gets a payout.
	for ownership in stock.ownership_set:
		user=ownership.account
		user.balance += COST_PER_BUNDLE * ownership.amount
		user.put()
	self.redirect("/")

class AtomFeedHandler(webapp.RequestHandler):

  def get(self):
  	trades = []
  	for trade in Trade.all():
		if trade.amount < 0:
			trade_type = "Buy"
		else:
			trade_type = "Sell"
		trades.append({
			"bundle" : trade.stock.bundle.name,
			"owner" : trade.account.user.nickname(),
			"ticker" : trade.stock.name,
			"type" : trade_type,
			"amount" : abs(trade.amount),
			"price" : trade.price,
			"updated" : datetime.datetime.now(),
			"name" : trade.stock.title,
			})
	self.response.out.write(template.render("trades.tmpl",{"trades":trades}))


def main():
  application = webapp.WSGIApplication([
  						('/', MainHandler),
						('/bundle', BundleHandler),
						('/newbundle', NewBundleHandler),
						('/resolve', ResolveHandler),
						('/trades', AtomFeedHandler),
					],
                                       debug=True)
  wsgiref.handlers.CGIHandler().run(application)


if __name__ == '__main__':
  main()
